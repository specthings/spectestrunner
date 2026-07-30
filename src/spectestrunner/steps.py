# SPDX-License-Identifier: BSD-2-Clause
""" Provides the step sequences which the run commands share. """

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

import dataclasses
import logging
import math
import signal
import time
from typing import Any, Callable, Iterator, Optional

import grpc

from .exitcodes import (EXIT_ACTION, EXIT_INTERRUPTED, EXIT_OK, EXIT_STATUS,
                        EXIT_TRANSPORT)

# pylint: disable=no-name-in-module
from .servicegrpc_pb2 import (  # type: ignore
    GRPCActionRequest, GRPCRunImageRequest)

#: The gRPC status codes which justify another attempt of the sequence.
TRANSIENT_CODES = frozenset([
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED,
    grpc.StatusCode.RESOURCE_EXHAUSTED,
    grpc.StatusCode.ABORTED,
])

#: The extra time granted to a call on top of the execution timeout.
CALL_TIMEOUT_MARGIN = 60.0

#: The longest a wait sleeps before it looks for a stop again.
WAIT_SLICE = 1.0

#: The step kind which runs an image on the target.
STEP_IMAGE = "image"

#: The step kind which requests an action of an agent.
STEP_ACTION = "action"

#: The step kind which delays the sequence.
STEP_WAIT = "wait"

#: The result status of a step which was not reached.
STATUS_SKIPPED = "skipped"

#: The prefix of the status of a step which succeeded.
ACTION_SUCCESS = "success"

#: The prefix of the status of a step which never reached the target.
#: The server reports its own failures with an 'error:' status, so a step
#: which did not run at all needs a prefix of its own.
STATUS_UNREACHED = "unreached: "


class TransientError(RuntimeError):
    """ This error indicates that the sequence should be attempted again. """


class StepError(RuntimeError):
    """ This error indicates that one step of a sequence failed for good. """


class Stopped(RuntimeError):
    """
    This error indicates that the sequence was stopped while it ran.

    Whatever ran the sequence decides what an unfinished sequence means.  The
    bridge leaves the request pending without counting an attempt, since the
    stop belongs to the bridge and not to the request.
    """


@dataclasses.dataclass
class Context:
    """
    Holds the values which every step of one sequence shares.

    The data holds the image of every image step by its index in the
    sequence.  Whoever builds the sequence fills it before the first step
    runs, so that a sequence which cannot be prepared activates nothing.
    """
    target: str
    timeout: float
    data: dict[int, bytes]


def succeeded(status: str) -> bool:
    """ Return whether the result status of a step indicates success. """
    return status.startswith(ACTION_SUCCESS)


def check_wait_seconds(seconds: Any) -> float:
    """
    Return the delay of a wait step in seconds.

    Whoever runs the step sleeps for exactly this long, so a value which is
    not a finite non-negative number would delay the sequence forever.
    """
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise ValueError("no 'seconds' number")
    if not math.isfinite(seconds) or seconds < 0.0:
        raise ValueError(f"no finite non-negative 'seconds': {seconds}")
    return float(seconds)


def describe_step(step: dict[str, Any]) -> str:
    """
    Return the step in a human readable form.

    This accepts a step of a request as well as a result of a response, since
    both carry the kind and the values which name the step.
    """
    kind = step.get("kind")
    if kind == STEP_ACTION:
        return f"action '{step.get('action')}' for {step.get('uid')}"
    if kind == STEP_WAIT:
        return f"wait of {step.get('seconds')} seconds"
    return f"image {step.get('path')}"


def continue_on_failure(step: dict[str, Any]) -> bool:
    """
    Return whether the sequence continues after this step failed.

    A failed image step produced the result the request asked for, so the
    remaining steps still run.  A failed action step falsified the
    precondition of everything after it, so the sequence stops.
    """
    return bool(step.get("continue_on_failure",
                         step.get("kind") == STEP_IMAGE))


def bare_result(step: dict[str, Any], status: str) -> dict[str, Any]:
    """ Return the result of a step which produced no output of its own. """
    result: dict[str, Any] = {"kind": step["kind"], "status": status}
    for key in ("path", "uid", "action", "seconds"):
        if key in step:
            result[key] = step[key]
    return result


def exit_status(results: list[dict[str, Any]],
                fail_on_status: Optional[str]) -> int:
    """
    Return the exit code which the results of a sequence deserve.

    A step which never reached its target outranks everything a step
    actually reported, since it produced no result of the work at all.  A
    failed action falsified the precondition of the steps after it.  Only a
    run of an image reports a status which the caller can expect of it, which
    is why a failed run needs the expectation to become an exit code.
    """
    for result in results:
        status = result.get("status", "")
        if status.startswith(STATUS_UNREACHED):
            logging.error("%s did not run: %s", describe_step(result), status)
            return EXIT_TRANSPORT
    for result in results:
        status = result.get("status", "")
        if (result.get("kind") == STEP_ACTION and status != STATUS_SKIPPED
                and not succeeded(status)):
            logging.error("%s failed: %s", describe_step(result), status)
            return EXIT_ACTION
    for result in results:
        if (fail_on_status is not None and result.get("kind") == STEP_IMAGE
                and result.get("status") != fail_on_status):
            logging.error("%s reported status '%s' instead of '%s'",
                          describe_step(result), result.get("status"),
                          fail_on_status)
            return EXIT_STATUS
    return EXIT_OK


def report_result(result: dict[str, Any]) -> None:
    """
    Report the result of one step.

    The output of a run goes to the standard output and everything else to
    the log, so that a caller can collect the output on its own.  One result
    at a time lets a command report a run the moment it completes.
    """
    if result.get("status") == STATUS_SKIPPED:
        logging.warning("skipped: %s", describe_step(result))
        return
    if result.get("kind") == STEP_ACTION:
        logging.info("%s -> status '%s'", describe_step(result),
                     result.get("status"))
        return
    if result.get("kind") == STEP_WAIT:
        logging.info("%s -> waited %s seconds", describe_step(result),
                     result.get("waited_in_seconds"))
        return
    logging.info("received result for: %s", result.get("path"))
    logging.info("result status: %s", result.get("status"))
    logging.info("load duration in seconds: %s",
                 result.get("load_duration_in_seconds"))
    logging.info("execution duration in seconds: %s",
                 result.get("execution_duration_in_seconds"))
    output = result.get("output")
    if output is not None:
        print(output.decode("latin-1"))


def _classify(err: grpc.RpcError) -> Exception:
    """
    Return the error which corresponds to the gRPC status code.

    A permanent failure belongs to the step and not to the sequence.  The
    steps before it ran and may have activated resources, so abandoning the
    whole sequence would hide what it already did.
    """
    code = err.code() if hasattr(err, "code") else None
    if code in TRANSIENT_CODES:
        return TransientError(f"{code}: {err}")
    return StepError(f"{code}: {err}")


def _run_image(stub: Any, context: Context, step: dict[str, Any],
               index: int) -> dict[str, Any]:
    """ Run one image step and return its result. """
    logging.info("run: %s on %s", step["path"], context.target)
    timeout = context.timeout
    try:
        response = stub.request_run_image(
            GRPCRunImageRequest(target_id=context.target,
                                breakpoints=step.get("breakpoints", []),
                                path=step["path"],
                                digest=step["digest"],
                                data=context.data[index],
                                execution_timeout_in_seconds=timeout),
            timeout=timeout + CALL_TIMEOUT_MARGIN)
    except grpc.RpcError as err:
        raise _classify(err) from err
    return {
        "kind":
        STEP_IMAGE,
        "path":
        step["path"],
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


def _run_action(stub: Any, context: Context,
                step: dict[str, Any]) -> dict[str, Any]:
    """ Request one action step and return its result. """
    logging.info("action: %s for %s", step["action"], step["uid"])

    # An action without a deadline can wedge whoever runs the sequence, and
    # the bridge runs the sequences of everybody one after the other.
    try:
        response = stub.request_action(
            GRPCActionRequest(uid=step["uid"], action=step["action"]),
            timeout=context.timeout + CALL_TIMEOUT_MARGIN)
    except grpc.RpcError as err:
        raise _classify(err) from err
    return {
        "kind": STEP_ACTION,
        "uid": step["uid"],
        "action": step["action"],
        "status": response.status,
    }


def _run_wait(step: dict[str, Any],
              is_stopped: Optional[Callable[[], bool]]) -> dict[str, Any]:
    """ Delay the sequence and return the result of the wait step. """
    seconds = float(step["seconds"])
    logging.info("wait: %ss", seconds)
    begin = time.monotonic()
    deadline = begin + seconds

    # Sleep in slices, so that a stop does not have to wait the whole delay
    # out.  A wait holds up whoever runs the sequence for its duration, and
    # the bridge serves everybody one request after the other.
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            break
        if is_stopped is not None and is_stopped():
            raise Stopped(f"stopped during the {describe_step(step)}")
        time.sleep(min(WAIT_SLICE, remaining))
    return {
        "kind": STEP_WAIT,
        "seconds": seconds,
        # A response commit is read by people, so keep the resolution of the
        # elapsed time at a millisecond instead of a float artefact.
        "waited_in_seconds": round(time.monotonic() - begin, 3),
        "status": ACTION_SUCCESS,
    }


def run_steps(
    stub: Any,
    context: Context,
    sequence: list[dict[str, Any]],
    is_stopped: Optional[Callable[[],
                                  bool]] = None) -> Iterator[dict[str, Any]]:
    """
    Yield the result of every step of the sequence in order.

    The steps after a failed one are yielded as skipped unless the failed
    step continues on failure.  A step which failed for good yields a result
    with an error status, since the steps before it ran and may have
    activated resources.  A failure which justifies another attempt of the
    whole sequence raises a transient error instead.

    A stop through is_stopped ends a wait with a stopped error, since a wait
    is doing nothing anyway.  A step which is doing work is never abandoned
    half way through, so whoever consumes this decides whether to ask for the
    step after it.
    """
    stopped = False
    for index, step in enumerate(sequence):
        if stopped:
            logging.debug("skip step %d of %d: %s", index, len(sequence),
                          describe_step(step))
            yield bare_result(step, STATUS_SKIPPED)
            continue
        try:
            if step["kind"] == STEP_ACTION:
                result = _run_action(stub, context, step)
            elif step["kind"] == STEP_WAIT:
                result = _run_wait(step, is_stopped)
            else:
                result = _run_image(stub, context, step, index)
        except StepError as err:
            result = bare_result(step, f"{STATUS_UNREACHED}{err}")

        # The result of an image step carries the whole output of the run, so
        # only its status is logged.
        logging.debug("step %d of %d: %s: status '%s'", index, len(sequence),
                      describe_step(step), result["status"])
        yield result
        if not succeeded(result["status"]) and not continue_on_failure(step):
            logging.warning("stop after step %d: %s", index, result["status"])
            stopped = True


def _report_the_rest(sequence: list[dict[str, Any]], done: int) -> int:
    """ Report the steps which never ran and return the exit code. """
    for step in sequence[done:]:
        report_result(bare_result(step, STATUS_SKIPPED))
    return EXIT_INTERRUPTED


def run_and_report(stub: Any,
                   context: Context,
                   sequence: list[dict[str, Any]],
                   fail_on_status: Optional[str] = None,
                   is_stopped: Optional[Callable[[], bool]] = None) -> int:
    """
    Run the sequence, report every result as it arrives, and return the exit
    code the results deserve.

    A stop ends the sequence before the step which follows the one that just
    completed, so an interrupt does not start work which nobody wants any
    more.  The steps which never ran are reported, so that whoever
    interrupted can see which resources are still activated.

    A stop which arrives during the last step leaves nothing to skip, so the
    results are complete and they decide the exit code as usual.
    """
    results: list[dict[str, Any]] = []
    try:
        for result in run_steps(stub, context, sequence, is_stopped):
            report_result(result)
            results.append(result)
            if (is_stopped is not None and is_stopped()
                    and len(results) < len(sequence)):
                logging.info("stopped after the %s", describe_step(result))
                return _report_the_rest(sequence, len(results))
    except TransientError as err:
        # Whoever attempts the sequence again decides what this means.  A
        # command which built the sequence itself has nothing to attempt.
        logging.error("%s", err)
        return EXIT_TRANSPORT
    except Stopped as err:
        logging.info("%s", err)
        return _report_the_rest(sequence, len(results))
    return exit_status(results, fail_on_status)


def stop_on_signal() -> Callable[[], bool]:
    """
    Install the signal handlers which stop a sequence.

    Returns the predicate which reports whether a stop was requested.
    """
    stopped = False

    def _stop(_signum, _frame):
        nonlocal stopped
        logging.info("stop requested, finishing the current step")
        stopped = True

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, _stop)
    return lambda: stopped
