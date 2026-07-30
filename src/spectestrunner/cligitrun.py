# SPDX-License-Identifier: BSD-2-Clause
""" Run a step sequence on a test server through a Git repository. """

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
import contextlib
import logging
import os
import sys
import tempfile
import time
from typing import Any, Iterator, Optional

from specitems import get_arguments

from spectestrunner import gitproto, gitwire, image

#: The round trip completed regardless of the reported run status.
EXIT_OK = 0

#: The bridge permanently refused the request.
EXIT_REJECTED = 1

#: No response arrived before the wait timeout expired.
EXIT_TIMEOUT = 2

#: A Git or transport operation failed.
EXIT_GIT = 3

#: A run reported a status other than the expected one.
EXIT_STATUS = 4

#: The request vanished before a response arrived.
EXIT_MISSING = 5

#: An action step failed, so the steps after it did not run.
EXIT_ACTION = 6


class _RequestGone(RuntimeError):
    """ This error indicates that the request no longer exists. """


class _BadUsage(RuntimeError):
    """ This error indicates that the command line is not usable. """


class _Step(argparse.Action):  # pylint: disable=too-few-public-methods
    """ Append the option value to the ordered step list. """

    def __call__(self, parser, namespace, values, option_string=None):
        kind = (gitproto.STEP_ACTION
                if option_string == "--action" else gitproto.STEP_IMAGE)
        namespace.steps.append({
            "kind": kind,
            "value": values,
            "continue_on_failure": None,
        })


class _OnFailure(argparse.Action):  # pylint: disable=too-few-public-methods
    """ Set the failure policy of the preceding step. """

    def __call__(self, parser, namespace, values, option_string=None):
        if not namespace.steps:
            parser.error(f"{option_string} has no preceding step")
        namespace.steps[-1]["continue_on_failure"] = (
            option_string == "--continue-on-failure")


def _get_arguments(argv: list[str]) -> argparse.Namespace:

    def _add_arguments(parser):
        parser.add_argument("--remote",
                            help="the Git remote used as transport",
                            required=True)
        parser.add_argument("--submitter",
                            help="the submitter identifier",
                            default=gitproto.get_default_submitter())
        parser.add_argument("--work-dir",
                            help="the local Git repository of the submitter",
                            default=None)
        parser.add_argument("--target",
                            help="the target identifier",
                            default="/does/not/exist")
        parser.add_argument("--timeout",
                            help="the execution timeout",
                            type=float,
                            default=180.0)
        parser.add_argument("--wait-timeout",
                            help="the timeout to wait for a response",
                            type=float,
                            default=3600.0)
        parser.add_argument("--poll-interval",
                            help="the response polling interval",
                            type=float,
                            default=10.0)
        parser.add_argument("--no-wait",
                            help="print the request identifier and exit",
                            action="store_true")
        parser.add_argument("--collect",
                            help="collect the response of this request",
                            default=None)
        parser.add_argument(
            "--fail-on-status",
            help="exit with a non-zero status if a run reports another status",
            default=None)
        parser.add_argument("--nm",
                            help="the path to the nm tool",
                            default="nm")
        parser.add_argument("--strip",
                            help="the path to the strip tool",
                            default="strip")
        # The step options share one destination so that their order on the
        # command line is the order of the sequence.  The default belongs to
        # the parser rather than to the first of them, which would make the
        # order of the add_argument calls significant.
        parser.set_defaults(steps=[])
        parser.add_argument("--action",
                            metavar="UID:ACTION",
                            dest="steps",
                            action=_Step,
                            help="append an action step to the sequence")
        parser.add_argument("--image",
                            metavar="IMAGE",
                            dest="steps",
                            action=_Step,
                            help="append an image step to the sequence")
        parser.add_argument("--continue-on-failure",
                            dest="steps",
                            action=_OnFailure,
                            nargs=0,
                            help="run the following steps although the "
                            "preceding step failed")
        parser.add_argument("--stop-on-failure",
                            dest="steps",
                            action=_OnFailure,
                            nargs=0,
                            help="skip the following steps if the preceding "
                            "step failed")
        parser.add_argument("images", nargs="*")

    return get_arguments(argv,
                         description=cligitrun.__doc__,
                         add_arguments=(_add_arguments, ))


@contextlib.contextmanager
def _repository(work_dir: Optional[str]) -> Iterator[gitwire.Repository]:
    if work_dir is None:
        with tempfile.TemporaryDirectory(prefix="spectestgitrun-") as tmp:
            repo = gitwire.Repository(os.path.join(tmp, "repo.git"))
            repo.init()
            yield repo
    else:
        repo = gitwire.Repository(work_dir)
        repo.init()
        yield repo


def _get_steps(args: argparse.Namespace) -> list[dict[str, Any]]:
    """ Return the ordered steps of the command line. """
    if args.images:
        if args.steps:
            raise _BadUsage("the image positionals have no defined order "
                            "with respect to the step options, use --image")
        return [{
            "kind": gitproto.STEP_IMAGE,
            "value": exe_path,
            "continue_on_failure": None,
        } for exe_path in args.images]
    return args.steps


def _make_action_step(value: str) -> dict[str, Any]:
    uid, _, action = value.partition(":")
    if not uid or not action:
        raise _BadUsage(f"'{value}' is no <uid>:<action> action")
    return {"kind": gitproto.STEP_ACTION, "uid": uid, "action": action}


def _make_image_step(repo: gitwire.Repository, args: argparse.Namespace,
                     entries: dict[str, Any], index: int,
                     exe_path: str) -> dict[str, Any]:
    data = image.strip_image(exe_path, args.strip)
    file = gitproto.image_file(index, os.path.basename(exe_path))
    entries.setdefault(gitproto.IMAGE_DIRECTORY,
                       {})[os.path.basename(file)] = (gitwire.MODE_EXECUTABLE,
                                                      repo.hash_object(data))
    logging.info("prepared: %s", exe_path)
    return {
        "kind": gitproto.STEP_IMAGE,
        "path": exe_path,
        "file": file,
        "digest": image.get_digest(data),
        "breakpoints": image.get_breakpoints(exe_path, args.nm),
    }


def _submit(repo: gitwire.Repository, args: argparse.Namespace) -> str:
    """ Push a request commit and return its identifier. """
    steps = []
    entries: dict[str, Any] = {}
    for index, wanted in enumerate(_get_steps(args)):
        if wanted["kind"] == gitproto.STEP_ACTION:
            step = _make_action_step(wanted["value"])
        else:
            step = _make_image_step(repo, args, entries, index,
                                    wanted["value"])
        if wanted["continue_on_failure"] is not None:
            step["continue_on_failure"] = wanted["continue_on_failure"]
        steps.append(step)
    message = gitproto.encode_request(args.submitter, args.target,
                                      args.timeout, steps)
    logging.debug("request message:\n%s", message)
    request_id = repo.commit_tree(repo.make_tree(entries), message)
    ref = gitproto.request_ref(args.submitter, request_id)
    repo.push(args.remote, f"{request_id}:{ref}")
    return request_id


def _wait_for_response(repo: gitwire.Repository, args: argparse.Namespace,
                       request_id: str) -> Optional[str]:
    """
    Return the response commit identifier, or None on timeout.  Raise a
    request gone error if the remote has neither a request nor a response
    reference, since then no response can ever arrive.
    """
    ref = gitproto.response_ref(request_id)
    pattern = gitproto.request_ref_pattern(request_id)
    deadline = time.monotonic() + args.wait_timeout
    polls = 0
    while True:
        refs = repo.remote_refs(args.remote, pattern, ref)
        polls += 1
        if ref in refs:
            logging.debug("response %s after %d polls", refs[ref], polls)
            repo.fetch(args.remote, f"+{ref}:{ref}")
            return refs[ref]
        if not refs:
            raise _RequestGone(
                f"the remote has neither a request nor a response reference "
                f"for {request_id}")
        if time.monotonic() >= deadline:
            return None
        logging.debug("poll %d: still waiting for %s, %.0fs left", polls, ref,
                      deadline - time.monotonic())
        time.sleep(
            min(args.poll_interval, max(0.0, deadline - time.monotonic())))


def _describe(result: dict[str, Any]) -> str:
    """ Return the step of the result in a human readable form. """
    if result.get("kind") == gitproto.STEP_ACTION:
        return f"action '{result.get('action')}' for {result.get('uid')}"
    return f"image {result.get('path')}"


def _report(repo: gitwire.Repository, commit: str, payload: dict[str,
                                                                 Any]) -> None:
    """ Print the results the same way the spectestrun command does. """
    blobs = repo.tree_entries(commit)
    for result in payload["results"]:
        if result.get("status") == gitproto.STATUS_SKIPPED:
            logging.warning("skipped: %s", _describe(result))
            continue
        if result.get("kind") == gitproto.STEP_ACTION:
            logging.info("%s -> status '%s'", _describe(result),
                         result.get("status"))
            continue
        logging.info("received result for: %s", result.get("path"))
        logging.info("result status: %s", result.get("status"))
        logging.info("load duration in seconds: %s",
                     result.get("load_duration_in_seconds"))
        logging.info("execution duration in seconds: %s",
                     result.get("execution_duration_in_seconds"))
        object_id = blobs.get(result.get("file", ""))
        if object_id is not None:
            print(repo.blob(object_id).decode("latin-1"))


def _cleanup(repo: gitwire.Repository, args: argparse.Namespace,
             request_id: str) -> None:
    """ Delete the request references first, then the response reference. """
    requests = repo.remote_refs(args.remote,
                                gitproto.request_ref_pattern(request_id))
    for ref in list(requests) + [gitproto.response_ref(request_id)]:
        try:
            repo.delete_remote_ref(args.remote, ref)
        except gitwire.GitError as err:
            logging.warning("could not delete %s: %s", ref, err)


def _exit_status(args: argparse.Namespace, payload: dict[str, Any]) -> int:
    if payload["status"] == gitproto.STATUS_REJECTED:
        logging.error("request rejected: %s",
                      payload.get("reason", "no reason given"))
        return EXIT_REJECTED
    results = payload["results"]
    for result in results:
        status = result.get("status", "")
        if (result.get("kind") == gitproto.STEP_ACTION
                and status != gitproto.STATUS_SKIPPED
                and not gitproto.succeeded(status)):
            logging.error("%s failed: %s", _describe(result), status)
            return EXIT_ACTION
    if args.fail_on_status is not None and any(
            result.get("status") != args.fail_on_status for result in results
            if result.get("kind") != gitproto.STEP_ACTION):
        return EXIT_STATUS
    return EXIT_OK


def _round_trip(repo: gitwire.Repository, args: argparse.Namespace) -> int:
    """ Submit a request if needed, then collect and report its response. """
    if args.collect is not None:
        request_id = args.collect
    else:
        request_id = _submit(repo, args)
        logging.info("submitted: %s", request_id)
        if args.no_wait:
            print(request_id)
            return EXIT_OK
    commit = _wait_for_response(repo, args, request_id)
    if commit is None:
        logging.error("no response for %s within %s seconds", request_id,
                      args.wait_timeout)
        return EXIT_TIMEOUT
    message = repo.commit_message(commit)
    logging.debug("response message:\n%s", message)
    payload = gitproto.decode_response(message)
    _report(repo, commit, payload)
    _cleanup(repo, args, request_id)
    return _exit_status(args, payload)


def cligitrun(argv: list[str] = sys.argv) -> int:
    """ Run a step sequence on a test server through a Git repository. """
    args = _get_arguments(argv[1:])
    if args.collect is None and not args.images and not args.steps:
        logging.error("no steps given")
        return EXIT_GIT
    try:
        with _repository(args.work_dir) as repo:
            return _round_trip(repo, args)
    except _BadUsage as err:
        logging.error("%s", err)
        return EXIT_GIT
    except _RequestGone as err:
        logging.error("%s", err)
        return EXIT_MISSING
    except gitproto.ProtocolError as err:
        logging.error("malformed response: %s", err)
        return EXIT_GIT
    except gitwire.GitError as err:
        logging.error("%s", err)
        return EXIT_GIT
