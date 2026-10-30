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
import logging
import math
import sys
from typing import Any

from specitems import get_arguments

from spectestrunner import (gitclient, gitproto, gitwire, image, stepargs,
                            steps)

# pylint: disable=unused-import
from spectestrunner.exitcodes import (  # noqa: F401
    EXIT_ACTION, EXIT_MISSING, EXIT_OK, EXIT_REJECTED, EXIT_STATUS,
    EXIT_TIMEOUT, EXIT_TRANSPORT, EXIT_USAGE)

#: A Git or transport operation failed.  This is the name of the transport
#: exit code which this command used before the codes were shared.
EXIT_GIT = EXIT_TRANSPORT


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
        stepargs.add_arguments(parser)

    return get_arguments(argv,
                         description=cligitrun.__doc__,
                         add_arguments=(_add_arguments, ))


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


def _submit(client: gitclient.Client, args: argparse.Namespace) -> str:
    """ Push a request commit and return its identifier. """
    request_steps, data = stepargs.build_steps(args)
    _warn_about_the_waits(args, request_steps)
    return client.submit_run_steps(args.target, args.timeout, request_steps,
                                   data)


def _exit_status(args: argparse.Namespace, payload: dict[str, Any]) -> int:
    """ Return the exit code of the response. """
    if payload["status"] == gitproto.STATUS_REJECTED:
        logging.error("request rejected: %s",
                      payload.get("reason", "no reason given"))
        return EXIT_REJECTED
    return steps.exit_status(payload["results"], args.fail_on_status)


def _round_trip(client: gitclient.Client, args: argparse.Namespace) -> int:
    """ Submit a request if needed, then collect and report its response. """
    if args.collect is not None:
        request_id = args.collect
    else:
        request_id = _submit(client, args)
        logging.info("submitted: %s", request_id)
        if args.no_wait:
            print(request_id)
            return EXIT_OK
    commit = client.wait_for_response(request_id)
    if commit is None:
        logging.error("no response for %s within %s seconds", request_id,
                      args.wait_timeout)
        return EXIT_TIMEOUT
    payload = client.read_response(commit)
    for result in payload["results"]:
        steps.report_result(result)
    client.cleanup(request_id)
    return _exit_status(args, payload)


def cligitrun(argv: list[str] = sys.argv) -> int:
    """ Run a step sequence on a test server through a Git repository. """
    args = _get_arguments(argv[1:])

    # A collection needs no steps of its own, since it reports the response
    # of a request which was submitted before.
    reason = stepargs.usage_error(args, need_steps=args.collect is None)
    if reason is not None:
        logging.error("%s", reason)
        return EXIT_USAGE
    try:
        with gitclient.repository(args.work_dir,
                                  prefix="spectestgitrun-") as repo:
            return _round_trip(
                gitclient.Client(repo, args.remote, args.submitter,
                                 args.wait_timeout, args.poll_interval), args)
    except (stepargs.UsageError, image.ImageError) as err:
        logging.error("%s", err)
        return EXIT_USAGE
    except gitclient.RequestGone as err:
        logging.error("%s", err)
        return EXIT_MISSING
    except gitproto.ProtocolError as err:
        logging.error("malformed response: %s", err)
        return EXIT_GIT
    except gitwire.GitError as err:
        logging.error("%s", err)
        return EXIT_GIT
