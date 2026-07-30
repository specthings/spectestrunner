# SPDX-License-Identifier: BSD-2-Clause
""" Request an action on a test server through gRPC. """

# Copyright (C) 2025 embedded brains GmbH & Co. KG
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
import sys
from typing import Any

from specitems import get_arguments

from spectestrunner import stepargs, steps
from spectestrunner.exitcodes import EXIT_USAGE
from spectestrunner.grpcclient import run_with_service


def _get_arguments(argv: list[str]) -> argparse.Namespace:

    def _add_arguments(parser):
        parser.add_argument("--timeout",
                            help="the execution timeout",
                            type=float,
                            default=180.0)
        parser.add_argument("--server-address",
                            help="the server address",
                            default="localhost:50051")
        parser.add_argument(
            "requests",
            metavar="REQUEST",
            help="the format of each request is <uid>:<action>",
            nargs="+")

    return get_arguments(argv,
                         description=cliaction.__doc__,
                         add_arguments=(_add_arguments, ))


def _get_steps(args: argparse.Namespace) -> list[dict[str, Any]]:
    """
    Return the action steps of the command line.

    The requests of this command are independent of each other rather than a
    sequence with preconditions, so a failed one does not skip the rest.  An
    operator who deactivates two resources wants both attempted.
    """
    sequence = []
    for request in args.requests:
        step = stepargs.make_action_step(request)
        step["continue_on_failure"] = True
        sequence.append(step)
    return sequence


def cliaction(argv: list[str] = sys.argv) -> int:
    """ Request an action on a test server through gRPC. """
    args = _get_arguments(argv[1:])
    try:
        sequence = _get_steps(args)
    except stepargs.UsageError as err:
        logging.error("%s", err)
        return EXIT_USAGE
    context = steps.Context(target="", timeout=args.timeout, data={})
    is_stopped = steps.stop_on_signal()
    return run_with_service(
        args.server_address, lambda stub: steps.run_and_report(
            stub, context, sequence, is_stopped=is_stopped))
