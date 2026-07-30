# SPDX-License-Identifier: BSD-2-Clause
""" Bridge a Git repository to a test server. """

# Copyright (C) 2026 embedded brains GmbH & Co. KG
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

import argparse
import dataclasses
import logging
import os
import signal
import sys
import time
from typing import Any, Optional

import grpc
from specitems import get_arguments

from spectestrunner import gitproto, gitwire, image

# pylint: disable=no-name-in-module
from spectestrunner import (  # type: ignore
    GRPCActionRequest, GRPCRunImageRequest, GRPCServiceStub)

#: The gRPC status codes which justify another attempt.
TRANSIENT_CODES = frozenset([
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED,
    grpc.StatusCode.RESOURCE_EXHAUSTED,
    grpc.StatusCode.ABORTED,
])

#: The extra time granted to a run on top of its execution timeout.
CALL_TIMEOUT_MARGIN = 60.0


class _TransientError(RuntimeError):
    """ This error indicates that the request should be attempted again. """


class _StepError(RuntimeError):
    """ This error indicates that one step of a request failed for good. """


@dataclasses.dataclass
class _Context:
    """ Holds the values which every step of one request shares. """
    target: str
    timeout: float
    data: dict[int, bytes]


def _classify(err: grpc.RpcError) -> Exception:
    """
    Return the error which corresponds to the gRPC status code.

    A permanent failure belongs to the step and not to the request.  The
    steps before it ran and may have activated resources, so a rejected
    response with no results would hide what the request did.
    """
    code = err.code() if hasattr(err, "code") else None
    if code in TRANSIENT_CODES:
        return _TransientError(f"{code}: {err}")
    return _StepError(f"{code}: {err}")


def _describe(step: dict[str, Any]) -> str:
    """ Return the step in a human readable form. """
    if step["kind"] == gitproto.STEP_ACTION:
        return f"action '{step['action']}' for {step['uid']}"
    return f"image {step['path']}"


def _bare_result(step: dict[str, Any], status: str) -> dict[str, Any]:
    """ Return the result of a step which produced no output of its own. """
    result = {"kind": step["kind"], "status": status}
    for key in ("path", "uid", "action"):
        if key in step:
            result[key] = step[key]
    return result


def _get_arguments(argv: list[str]) -> argparse.Namespace:

    def _add_arguments(parser):
        parser.add_argument("--remote",
                            help="the Git remote used as transport",
                            required=True)
        parser.add_argument("--work-dir",
                            help="the local Git repository of the bridge",
                            required=True)
        parser.add_argument("--server-address",
                            help="the server address",
                            default="localhost:50051")
        parser.add_argument("--poll-interval",
                            help="the remote polling interval",
                            type=float,
                            default=15.0)
        parser.add_argument("--response-retention",
                            help="the retention of responses in seconds",
                            type=float,
                            default=86400.0)
        parser.add_argument("--max-attempts",
                            help="the attempts per request before rejection",
                            type=int,
                            default=3)
        parser.add_argument("--once",
                            help="process the pending requests and exit",
                            action="store_true")

    return get_arguments(argv,
                         description=cligitbridge.__doc__,
                         add_arguments=(_add_arguments, ))


class Bridge:
    """ Polls a Git remote for requests and answers them through gRPC. """

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.repo = gitwire.Repository(args.work_dir)
        self.repo.init()
        self.attempts: dict[str, int] = {}
        self.stop = False

    def _refs(self) -> tuple[dict[str, list[str]], dict[str, str]]:
        """ Return the request references and responses by identifier. """
        remote = self.repo.remote_refs(self.args.remote, gitproto.REF_PATTERN)
        local = self.repo.local_refs(gitproto.REF_PREFIX)
        if remote != local:
            logging.debug("remote references changed, fetching")
            self.repo.fetch(self.args.remote,
                            f"+{gitproto.REF_PATTERN}:{gitproto.REF_PATTERN}",
                            prune=True)
            local = self.repo.local_refs(gitproto.REF_PREFIX)
        requests: dict[str, list[str]] = {}
        responses: dict[str, str] = {}
        for ref, commit in local.items():
            if ref.startswith(gitproto.REQUESTS_PREFIX + "/"):
                try:
                    _, request_id = gitproto.split_request_ref(ref)
                except gitproto.ProtocolError as err:
                    logging.warning("ignoring %s: %s", ref, err)
                    continue
                if request_id != commit:
                    logging.warning(
                        "ignoring %s: it does not name its own "
                        "commit %s", ref, commit)
                    continue
                requests.setdefault(request_id, []).append(ref)
            elif ref.startswith(gitproto.RESPONSES_PREFIX + "/"):
                try:
                    responses[gitproto.split_response_ref(ref)] = commit
                except gitproto.ProtocolError as err:
                    logging.warning("ignoring %s: %s", ref, err)
        return requests, responses

    def _run_steps(self, commit: str,
                   payload: dict[str, Any]) -> list[dict[str, Any]]:
        """ Run the steps of the request in order and return the results. """
        gitproto.check_run_steps_request(payload)
        steps = payload["steps"]
        context = _Context(target=payload["target"],
                           timeout=float(payload["timeout"]),
                           data=self._load_images(commit, steps))
        results = []
        stopped = False
        with grpc.insecure_channel(self.args.server_address) as channel:
            stub = GRPCServiceStub(channel)
            for index, step in enumerate(steps):
                if stopped:
                    logging.debug("skip step %d of %d: %s", index, len(steps),
                                  _describe(step))
                    results.append(_bare_result(step, gitproto.STATUS_SKIPPED))
                    continue
                try:
                    if step["kind"] == gitproto.STEP_ACTION:
                        result = self._run_action(stub, context, step)
                    else:
                        result = self._run_image(stub, context, step, index)
                except _StepError as err:
                    result = _bare_result(step, f"error: {err}")
                results.append(result)

                # The result of an image step carries the whole output of the
                # run, so only its status is logged.
                logging.debug("step %d of %d: %s: status '%s'", index,
                              len(steps), _describe(step), result["status"])
                if not gitproto.succeeded(
                        result["status"]) and not gitproto.continue_on_failure(
                            step):
                    logging.warning("stop after step %d: %s", index,
                                    result["status"])
                    stopped = True
        return results

    def _load_images(self, commit: str,
                     steps: list[dict[str, Any]]) -> dict[int, bytes]:
        """
        Return the image data of the request by step index.

        Every image is fetched and checked before the first step runs.  A
        request which is rejected half way through has already activated
        resources which its response cannot mention.
        """
        blobs = self.repo.tree_entries(commit)
        data = {}
        for index, step in enumerate(steps):
            if step["kind"] != gitproto.STEP_IMAGE:
                continue
            object_id = blobs.get(step["file"])
            if object_id is None:
                raise gitproto.ProtocolError(
                    f"the commit has no '{step['file']}' file")
            content = self.repo.blob(object_id)
            digest = image.get_digest(content)
            logging.debug("step %d: '%s' has %d bytes and digest %s", index,
                          step["file"], len(content), digest)
            if digest != step["digest"]:
                raise gitproto.ProtocolError(
                    f"'{step['file']}' has digest {digest} instead of "
                    f"{step['digest']}")
            data[index] = content
        return data

    def _run_image(self, stub: Any, context: "_Context", step: dict[str, Any],
                   index: int) -> dict[str, Any]:
        """ Run one image step and return its result. """
        data = context.data[index]
        logging.info("run: %s on %s", step["path"], context.target)
        response = self._request_run_image(stub, context, step, data)
        return {
            "kind":
            gitproto.STEP_IMAGE,
            "path":
            step["path"],
            "file":
            gitproto.output_file(index, os.path.basename(step["path"])),
            "target":
            response.target_id,
            "digest":
            step["digest"],
            "status":
            response.status,
            "load_duration_in_seconds":
            float(response.load_duration_in_seconds),
            "execution_duration_in_seconds":
            float(response.execution_duration_in_seconds),
            "output":
            response.output,
        }

    def _run_action(self, stub: Any, context: "_Context",
                    step: dict[str, Any]) -> dict[str, Any]:
        """ Request one action step and return its result. """
        logging.info("action: %s for %s", step["action"], step["uid"])
        response = self._request_action(stub, context, step)
        return {
            "kind": gitproto.STEP_ACTION,
            "uid": step["uid"],
            "action": step["action"],
            "status": response.status,
        }

    @staticmethod
    def _request_action(stub: Any, context: "_Context",
                        step: dict[str, Any]) -> Any:
        # An action without a deadline can wedge the bridge, and the bridge
        # runs the requests of everybody one after the other.
        try:
            return stub.request_action(
                GRPCActionRequest(uid=step["uid"], action=step["action"]),
                timeout=context.timeout + CALL_TIMEOUT_MARGIN)
        except grpc.RpcError as err:
            raise _classify(err) from err

    @staticmethod
    def _request_run_image(stub: Any, context: "_Context",
                           entry: dict[str, Any], data: bytes) -> Any:
        timeout = context.timeout
        try:
            return stub.request_run_image(
                GRPCRunImageRequest(target_id=context.target,
                                    breakpoints=entry.get("breakpoints", []),
                                    path=entry["path"],
                                    digest=entry["digest"],
                                    data=data,
                                    execution_timeout_in_seconds=timeout),
                timeout=timeout + CALL_TIMEOUT_MARGIN)
        except grpc.RpcError as err:
            raise _classify(err) from err

    def _push_response(self, request_id: str, status: str,
                       results: list[dict[str, Any]],
                       reason: Optional[str]) -> None:
        """ Build and push the response commit of the request. """
        entries: dict[str, Any] = {}
        payload = []
        for result in results:
            record = dict(result)
            output = record.pop("output", None)
            if output is not None:
                entries.setdefault(
                    gitproto.OUTPUT_DIRECTORY, {})[os.path.basename(
                        record["file"])] = (gitwire.MODE_FILE,
                                            self.repo.hash_object(output))
            payload.append(record)
        message = gitproto.encode_response(request_id, status, payload, reason)
        logging.debug("response message:\n%s", message)
        commit = self.repo.commit_tree(self.repo.make_tree(entries), message)
        self.repo.create_remote_ref(self.args.remote,
                                    gitproto.response_ref(request_id), commit)
        logging.info("responded to %s with '%s'", request_id, status)

    def _is_answered(self, request_id: str) -> bool:
        """ Return whether the remote already has a response. """
        ref = gitproto.response_ref(request_id)
        try:
            return ref in self.repo.remote_refs(self.args.remote, ref)
        except gitwire.GitError:
            return False

    def _publish(self, request_id: str, status: str,
                 results: list[dict[str, Any]], reason: Optional[str]) -> bool:
        """ Push the response and return whether the request is answered. """
        try:
            self._push_response(request_id, status, results, reason)
        except gitwire.GitError as err:
            if not self._is_answered(request_id):
                logging.error("could not answer %s: %s", request_id, err)
                return False
            logging.warning("%s is already answered by another bridge",
                            request_id)
        self.attempts.pop(request_id, None)
        return True

    def _count_attempt(self, request_id: str, reason: object) -> None:
        """ Record a failed attempt and reject the request past the limit. """
        attempt = self.attempts.get(request_id, 0) + 1
        self.attempts[request_id] = attempt
        if attempt < self.args.max_attempts:
            logging.warning("attempt %d for %s failed: %s", attempt,
                            request_id, reason)
            return
        logging.error("reject %s after %d attempts: %s", request_id, attempt,
                      reason)
        self._publish(request_id, gitproto.STATUS_REJECTED, [],
                      f"{attempt} attempts failed, last: {reason}")

    def _process(self, request_id: str) -> None:
        """ Run one request and push its response. """
        try:
            message = self.repo.commit_message(request_id)
            logging.debug("request %s message:\n%s", request_id, message)
            payload = gitproto.decode_request(message)
            kind = payload["kind"]
            logging.debug("request %s is '%s' from '%s' for target '%s'",
                          request_id, kind, payload.get("submitter"),
                          payload.get("target"))
            if kind != gitproto.KIND_RUN_STEPS:
                raise gitproto.ProtocolError(f"unsupported kind '{kind}'")
            results = self._run_steps(request_id, payload)
        except gitproto.ProtocolError as err:
            logging.error("reject %s: %s", request_id, err)
            self._publish(request_id, gitproto.STATUS_REJECTED, [], str(err))
        except (_TransientError, gitwire.GitError) as err:
            self._count_attempt(request_id, err)
        else:
            if not self._publish(request_id, gitproto.STATUS_COMPLETED,
                                 results, None):
                self._count_attempt(request_id, "the response was not pushed")

    def _age(self, commit: str) -> float:
        """ Return the age of the commit in seconds, or zero if unreadable. """
        try:
            return time.time() - self.repo.commit_time(commit)
        except gitwire.GitError as err:
            logging.warning("could not read the time of %s: %s", commit, err)
            return 0.0

    def _reap(self, requests: dict[str, list[str]],
              responses: dict[str, str]) -> None:
        """ Delete the references of requests past the retention. """
        for request_id, commit in responses.items():
            if self._age(commit) < self.args.response_retention:
                continue
            logging.info("reap answered %s", request_id)
            self._delete(gitproto.response_ref(request_id))
            for ref in requests.get(request_id, []):
                self._delete(ref)
        for request_id, refs in requests.items():
            if request_id in responses:
                continue
            if self._age(request_id) < self.args.response_retention:
                continue
            logging.warning("reap abandoned %s", request_id)
            for ref in refs:
                self._delete(ref)

    def _delete(self, ref: str) -> None:
        try:
            self.repo.delete_remote_ref(self.args.remote, ref)
        except gitwire.GitError as err:
            logging.warning("could not delete %s: %s", ref, err)

    def tick(self) -> None:
        """ Poll the remote once and process everything which is pending. """
        requests, responses = self._refs()
        pending = [
            request_id for request_id in requests
            if request_id not in responses
        ]
        pending.sort(
            key=lambda request_id: (-self._age(request_id), request_id))
        logging.debug("tick: %d requests, %d answered, pending %s",
                      len(requests), len(responses), pending)
        for request_id in pending:
            if self.stop:
                logging.debug("stop before %s", request_id)
                return
            self._process(request_id)
        self._reap(requests, responses)

    def run(self) -> None:
        """ Poll the remote until the process is asked to stop. """
        while not self.stop:
            try:
                self.tick()
            except gitwire.GitError as err:
                logging.error("%s", err)
            if self.args.once:
                return
            deadline = time.monotonic() + self.args.poll_interval
            while not self.stop and time.monotonic() < deadline:
                time.sleep(min(1.0, deadline - time.monotonic()))


def cligitbridge(argv: list[str] = sys.argv) -> int:
    """ Bridge a Git repository to a test server. """
    args = _get_arguments(argv[1:])
    bridge = Bridge(args)

    def _stop(_signum, _frame):
        logging.info("stop requested, finishing the current run")
        bridge.stop = True

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, _stop)
    bridge.run()
    return 0
