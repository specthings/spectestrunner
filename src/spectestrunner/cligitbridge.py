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
import logging
import os
import signal
import sys
import time
from typing import Any, Optional

import grpc
from specitems import get_arguments

from spectestrunner import gitproto, gitwire, image, steps
from spectestrunner.exitcodes import EXIT_OK

# pylint: disable=no-name-in-module
from spectestrunner import GRPCServiceStub  # type: ignore


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
        request_steps = payload["steps"]
        context = steps.Context(target=payload["target"],
                                timeout=float(payload["timeout"]),
                                data=self._load_images(commit, request_steps))

        # The channel belongs to the caller, since a sequence which is not
        # run to its end would otherwise leave it to the garbage collector.
        with grpc.insecure_channel(self.args.server_address) as channel:
            return list(
                steps.run_steps(GRPCServiceStub(channel), context,
                                request_steps, lambda: self.stop))

    def _load_images(self, commit: str,
                     request_steps: list[dict[str, Any]]) -> dict[int, bytes]:
        """
        Return the image data of the request by step index.

        Every image is fetched and checked before the first step runs.  A
        request which is rejected half way through has already activated
        resources which its response cannot mention.
        """
        blobs = self.repo.tree_entries(commit)
        data = {}
        for index, step in enumerate(request_steps):
            if step["kind"] != steps.STEP_IMAGE:
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

    def _push_response(self, request_id: str, status: str,
                       results: list[dict[str, Any]],
                       reason: Optional[str]) -> None:
        """
        Build and push the response commit of the request.

        The output of a run goes into a blob of the commit, so the result
        gains the name of that file here.  A step result on its own carries
        the bytes and knows nothing about a commit.
        """
        entries: dict[str, Any] = {}
        payload = []
        for index, result in enumerate(results):
            record = dict(result)
            output = record.pop("output", None)
            if output is not None:
                file = gitproto.output_file(index,
                                            os.path.basename(record["path"]))
                record["file"] = file
                entries.setdefault(gitproto.OUTPUT_DIRECTORY,
                                   {})[os.path.basename(file)] = (
                                       gitwire.MODE_FILE,
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
        except steps.Stopped as err:
            logging.info("%s, so %s stays pending", err, request_id)
        except gitproto.ProtocolError as err:
            logging.error("reject %s: %s", request_id, err)
            self._publish(request_id, gitproto.STATUS_REJECTED, [], str(err))
        except (steps.TransientError, gitwire.GitError) as err:
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
    return EXIT_OK
