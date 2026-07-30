# SPDX-License-Identifier: BSD-2-Clause
""" Build a step sequence from a command line. """

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
from typing import Any, Optional

from . import image, steps


class UsageError(RuntimeError):
    """ This error indicates that the command line is not usable. """


#: The step kind of each option which appends a step.
_STEP_KIND_BY_OPTION = {
    "--action": steps.STEP_ACTION,
    "--image": steps.STEP_IMAGE,
    "--wait": steps.STEP_WAIT,
}


class _Step(argparse.Action):  # pylint: disable=too-few-public-methods
    """ Append the option value to the ordered step list. """

    def __call__(self, parser, namespace, values, option_string=None):
        namespace.steps.append({
            "kind": _STEP_KIND_BY_OPTION[option_string],
            "value": values,
            "continue_on_failure": None,
        })


class _OnFailure(argparse.Action):  # pylint: disable=too-few-public-methods
    """ Set the failure policy of the preceding step. """

    def __call__(self, parser, namespace, values, option_string=None):
        # The commands report their own usage errors, so record the problem
        # instead of letting argparse exit while it parses.
        if not namespace.steps:
            namespace.bad_usage = f"{option_string} has no preceding step"
            return
        namespace.steps[-1]["continue_on_failure"] = (
            option_string == "--continue-on-failure")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """
    Add the options which every command that runs a step sequence shares.

    Only the options of the transport belong to a command of its own, so
    whatever is added here reaches all of them or none of them.
    """
    parser.add_argument("--target",
                        help="the target identifier",
                        default="/does/not/exist")
    parser.add_argument("--timeout",
                        help="the execution timeout",
                        type=float,
                        default=180.0)
    parser.add_argument("--nm", help="the path to the nm tool", default="nm")
    parser.add_argument("--strip",
                        help="the path to the strip tool",
                        default="strip")
    parser.add_argument(
        "--fail-on-status",
        help="exit with a non-zero status if a run reports another status",
        default=None)

    # The step options share one destination so that their order on the
    # command line is the order of the sequence.  The default belongs to the
    # parser rather than to the first of them, which would make the order of
    # the add_argument calls significant.
    parser.set_defaults(steps=[], bad_usage=None)
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
    # The value stays a string so that a malformed one yields the exit code
    # of the command instead of the exit code of argparse.
    parser.add_argument("--wait",
                        metavar="SECONDS",
                        dest="steps",
                        action=_Step,
                        help="append a step which delays the sequence")
    parser.add_argument("--continue-on-failure",
                        dest="steps",
                        action=_OnFailure,
                        nargs=0,
                        help="run the following steps although the preceding "
                        "step failed")
    parser.add_argument("--stop-on-failure",
                        dest="steps",
                        action=_OnFailure,
                        nargs=0,
                        help="skip the following steps if the preceding step "
                        "failed")
    parser.add_argument("images", nargs="*")


def usage_error(args: argparse.Namespace,
                need_steps: bool = True) -> Optional[str]:
    """ Return the reason why the command line is not usable, if any. """
    if args.bad_usage is not None:
        return args.bad_usage
    if need_steps and not args.images and not args.steps:
        return "no steps given"
    return None


def make_action_step(value: str) -> dict[str, Any]:
    """ Return the action step of a uid and action pair. """
    uid, _, action = value.partition(":")
    if not uid or not action:
        raise UsageError(f"'{value}' is no <uid>:<action> action")
    return {"kind": steps.STEP_ACTION, "uid": uid, "action": action}


def _make_wait_step(value: str) -> dict[str, Any]:
    try:
        seconds = steps.check_wait_seconds(float(value))
    except ValueError as err:
        raise UsageError(f"'{value}' is no wait in seconds: {err}") from err
    return {"kind": steps.STEP_WAIT, "seconds": seconds}


def _make_image_step(args: argparse.Namespace,
                     exe_path: str) -> tuple[dict[str, Any], bytes]:
    data = image.strip_image(exe_path, args.strip)
    logging.info("prepared: %s", exe_path)
    return {
        "kind": steps.STEP_IMAGE,
        "path": exe_path,
        "digest": image.get_digest(data),
        "breakpoints": image.get_breakpoints(exe_path, args.nm),
    }, data


def _get_wanted_steps(args: argparse.Namespace) -> list[dict[str, Any]]:
    """ Return the steps the command line asked for, in its order. """
    if args.images:
        if args.steps:
            raise UsageError("the image positionals have no defined order "
                             "with respect to the step options, use --image")
        return [{
            "kind": steps.STEP_IMAGE,
            "value": exe_path,
            "continue_on_failure": None,
        } for exe_path in args.images]
    return args.steps


def build_steps(
        args: argparse.Namespace
) -> tuple[list[dict[str, Any]], dict[int, bytes]]:
    """
    Return the sequence of the command line and its images by step index.

    Every image is prepared before the first step runs, so that a typo in the
    last one of them costs nothing on the bench.
    """
    sequence = []
    data = {}
    for index, wanted in enumerate(_get_wanted_steps(args)):
        if wanted["kind"] == steps.STEP_ACTION:
            step = make_action_step(wanted["value"])
        elif wanted["kind"] == steps.STEP_WAIT:
            step = _make_wait_step(wanted["value"])
        else:
            step, data[index] = _make_image_step(args, wanted["value"])
        if wanted["continue_on_failure"] is not None:
            step["continue_on_failure"] = wanted["continue_on_failure"]
        sequence.append(step)
    return sequence, data
