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
import math
import os
import sys
import tempfile
import time
from typing import Any, Iterator, Optional

from specitems import get_arguments

from spectestrunner import gitproto, gitwire, image, steps

# pylint: disable=unused-import
from spectestrunner.exitcodes import (  # noqa: F401
    EXIT_ACTION, EXIT_MISSING, EXIT_OK, EXIT_REJECTED, EXIT_STATUS,
    EXIT_TIMEOUT, EXIT_TRANSPORT, EXIT_USAGE)

#: A Git or transport operation failed.  This is the name of the transport
#: exit code which this command used before the codes were shared.
EXIT_GIT = EXIT_TRANSPORT


class _RequestGone(RuntimeError):
    """ This error indicates that the request no longer exists. """


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
        steps.add_arguments(parser)

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


def _warn_about_the_waits(args: argparse.Namespace,
                          request_steps: list[dict[str, Any]]) -> None:
    """
    Warn if the waits alone outlast the response wait of the submitter.

    The images and the actions take time as well, so this is a lower bound.
    """
    if args.no_wait:
        return
    total = math.fsum(step["seconds"] for step in request_steps
                      if step["kind"] == steps.STEP_WAIT)
    if total > args.wait_timeout:
        logging.warning(
            "the waits alone take %ss, which is longer than the wait timeout "
            "of %ss, so collect the response later with --collect", total,
            args.wait_timeout)


def _submit(repo: gitwire.Repository, args: argparse.Namespace) -> str:
    """ Push a request commit and return its identifier. """
    request_steps, data = steps.build_steps(args)

    # Only a commit stores an image as a file, so the step gains the name of
    # that file here rather than where the sequence is built.
    entries: dict[str, Any] = {}
    for index, content in data.items():
        file = gitproto.image_file(
            index, os.path.basename(request_steps[index]["path"]))
        request_steps[index]["file"] = file
        entries.setdefault(
            gitproto.IMAGE_DIRECTORY,
            {})[os.path.basename(file)] = (gitwire.MODE_EXECUTABLE,
                                           repo.hash_object(content))
    _warn_about_the_waits(args, request_steps)
    message = gitproto.encode_request(args.submitter, args.target,
                                      args.timeout, request_steps)
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


def _report(repo: gitwire.Repository, commit: str, payload: dict[str,
                                                                 Any]) -> None:
    """
    Report the results of the response.

    The output of a run lives in a blob of the response commit, so it is read
    back into the result.  A result carries the bytes everywhere else, which
    is what the reporting of a step expects.
    """
    blobs = repo.tree_entries(commit)
    for result in payload["results"]:
        object_id = blobs.get(result.get("file", ""))
        if object_id is not None:
            result["output"] = repo.blob(object_id)
        steps.report_result(result)


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
    """ Return the exit code of the response. """
    if payload["status"] == gitproto.STATUS_REJECTED:
        logging.error("request rejected: %s",
                      payload.get("reason", "no reason given"))
        return EXIT_REJECTED
    return steps.exit_status(payload["results"], args.fail_on_status)


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

    # A collection needs no steps of its own, since it reports the response
    # of a request which was submitted before.
    reason = steps.usage_error(args, need_steps=args.collect is None)
    if reason is not None:
        logging.error("%s", reason)
        return EXIT_USAGE
    try:
        with _repository(args.work_dir) as repo:
            return _round_trip(repo, args)
    except (steps.UsageError, image.ImageError) as err:
        logging.error("%s", err)
        return EXIT_USAGE
    except _RequestGone as err:
        logging.error("%s", err)
        return EXIT_MISSING
    except gitproto.ProtocolError as err:
        logging.error("malformed response: %s", err)
        return EXIT_GIT
    except gitwire.GitError as err:
        logging.error("%s", err)
        return EXIT_GIT
