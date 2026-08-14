# SPDX-License-Identifier: BSD-2-Clause
""" Test the commands which use the gRPC service. """

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

from concurrent import futures
import os
import signal
import time

import grpc
import pytest

from spectestrunner import (cliaction, clidescribetarget, cliio, clilog,
                            clirun, steps)
from spectestrunner.exitcodes import (EXIT_ACTION, EXIT_INTERRUPTED, EXIT_OK,
                                      EXIT_STATUS, EXIT_TRANSPORT, EXIT_USAGE)

# pylint: disable=no-name-in-module
from spectestrunner import (add_GRPCServiceServicer_to_server,
                            GRPCActionResponse, GRPCDescribeTargetResponse,
                            GRPCInputResponse, GRPCLogResponse,
                            GRPCRunImageResponse, GRPCServiceServicer)

from .util import write_image_tools

#: The target which the server runs images for.
TARGET_ID = "some/target"

#: The agent which streams input without an end.
STREAMING_UID = "/input/serial"

#: The agent which streams nothing at all.
SILENT_UID = "/input/dummy"

#: The logger which the server has no record of.
SILENT_LOGGER = "some.silent.logger"

#: The line which the log stream of the server carries.
LOG_LINE = "a message for the log stream"

#: The address of no server at all.  Port 0 asks the kernel for a free port
#: when a server binds it, so nothing ever listens on it and a connection to
#: it always fails.  A port next to the one of the server would be free only
#: by chance.
DEAD_ADDRESS = "localhost:0"

#: The real sleep.  A test patches time.sleep to signal the command, and
#: spectestrunner.steps.time is the time module itself, so the patch reaches
#: the whole process.  The server keeps the one it started with, otherwise a
#: stream of it would signal the command too.
_sleep = time.sleep


def _action_status(uid: str, action: str) -> str:
    """ Answer an action the way the protocol allows. """
    if uid.startswith("/no/"):
        return "error: no such agent"
    if uid == "/switch/lazy-0":
        return "error: the switch is broken"
    if action == "status":
        return "success: inactive"
    return "success"


class _Servicer(GRPCServiceServicer):
    """
    Answers the requests of the commands.

    The commands are what these tests exercise, so the answers are the ones
    the protocol allows.  Which answer a particular agent gives is a property
    of the server and is tested there.
    """

    def __init__(self):
        #: Signals the command once while it waits for the answer to a run.
        self.interrupt_on_run = False

        #: The requests to run an image, in the order they arrived.
        self.run_requests = []

    def request_action(self, request, context):
        return GRPCActionResponse(uid=request.uid,
                                  action=request.action,
                                  status=_action_status(
                                      request.uid, request.action))

    def request_describe_target(self, request, context):
        return GRPCDescribeTargetResponse(
            target_id=request.target_id,
            description=f"The target ``{request.target_id}`` is described.")

    def request_run_image(self, request, context):
        self.run_requests.append(request)

        # The command is inside the call, so the signal lands where an
        # interrupt of a run lands: in a step which is doing its work.
        if self.interrupt_on_run:
            self.interrupt_on_run = False
            os.kill(os.getpid(), signal.SIGINT)
        if request.target_id == TARGET_ID:
            status = "success"
            output = b"drain"
        else:
            # The image never ran, so the response carries no output of one.
            status = (f"{steps.STATUS_UNREACHED}no image runner for target "
                      f"'{request.target_id}'")
            output = b""
        return GRPCRunImageResponse(target_id=request.target_id,
                                    path=request.path,
                                    digest=request.digest,
                                    output=output,
                                    status=status,
                                    load_duration_in_seconds=0.0,
                                    execution_duration_in_seconds=0.0)

    def request_input(self, request, context):
        # An agent which is unknown yields an empty stream, which ends the
        # call at once.  The other two run until the client drops the call.
        if request.uid == STREAMING_UID:
            while context.is_active():
                yield GRPCInputResponse(uid=request.uid, data=b"tick\n")
                _sleep(0.01)
        elif request.uid == SILENT_UID:
            while context.is_active():
                _sleep(0.01)

    def request_log(self, request, context):
        if request.logger_name == SILENT_LOGGER:
            while context.is_active():
                _sleep(0.01)
            return
        while context.is_active():
            yield GRPCLogResponse(data=f"{LOG_LINE}\n")
            _sleep(0.01)


class _Bench:
    """ Provides a running server and the address of it. """

    def __init__(self, server, servicer, port, tmp_path):
        self._server = server
        self.servicer = servicer
        self.tmp_path = tmp_path
        self.address = f"localhost:{port}"
        self.nm, self.strip = write_image_tools(tmp_path / "tools")

    def image(self, name, content):
        """ Create a stand-in for an executable and return its path. """
        path = self.tmp_path / name
        path.write_bytes(content)
        return str(path)

    def stop(self):
        """ Stop the server, so the next call to it finds nothing. """
        self._server.stop(None).wait()


@pytest.fixture(name="bench")
def _bench(tmp_path, monkeypatch):
    """ Provide a started server in a fresh work directory. """
    monkeypatch.chdir(tmp_path)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    servicer = _Servicer()
    add_GRPCServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("localhost:0")
    server.start()
    yield _Bench(server, servicer, port, tmp_path)
    server.stop(None)


def _describe_target(bench, target_id):
    return clidescribetarget.clidescribetarget([
        "spectestdescribetarget", "--server-address", bench.address, target_id
    ])


def _action(bench, *requests):
    return cliaction.cliaction(
        ["spectestaction", "--server-address", bench.address, *requests])


def test_describe_target(bench, capsys):
    """ The description which the server returns is printed. """
    _describe_target(bench, TARGET_ID)
    assert f"target ``{TARGET_ID}``" in capsys.readouterr().out


def test_action_status(bench, capsys):
    """ The status of an action is reported. """
    _action(bench, "/input/dummy:status")
    assert "status 'success: inactive'" in capsys.readouterr().err


def test_action_of_an_unknown_agent(bench, capsys):
    """ An action which the server refuses is reported. """
    _action(bench, "/no/such/agent:status")
    assert "no such agent" in capsys.readouterr().err


def test_several_actions_in_one_call(bench, capsys):
    """ Each request of the command line is sent. """
    _action(bench, "/input/dummy:status", "/switch/dummy-0:status")
    err = capsys.readouterr().err
    assert "/input/dummy" in err
    assert "/switch/dummy-0" in err


def test_a_failed_action_does_not_skip_the_others(bench, capsys):
    """ The requests are independent, so a failed one skips nothing. """
    assert _action(bench, "/no/such/agent:status",
                   "/input/dummy:status") == EXIT_ACTION
    err = capsys.readouterr().err
    assert "no such agent" in err
    assert "status 'success: inactive'" in err
    assert "skipped" not in err


def test_an_action_which_is_no_pair(bench, capsys):
    """ A request which is no uid and action pair is a usage error. """
    assert _action(bench, "just-a-uid") == EXIT_USAGE
    assert "is no <uid>:<action>" in capsys.readouterr().err


def _run(bench, *images, target=TARGET_ID, extra=()):
    return clirun.clirun([
        "spectestrun", "--server-address", bench.address, "--target", target,
        "--nm", bench.nm, "--strip", bench.strip, *extra, *images
    ])


def test_run_image(bench, capsys):
    """ An image is sent to the runner of the target and answered. """
    _run(bench, bench.image("ticker.exe", b"\x7fELF-ticker"))
    captured = capsys.readouterr()
    assert "drain" in captured.out
    assert "result status: success" in captured.err


def test_run_several_images(bench, capsys):
    """ Each image of the command line is sent. """
    _run(bench, bench.image("a.exe", b"\x7fELF-a"),
         bench.image("b.exe", b"\x7fELF-b"))
    err = capsys.readouterr().err
    assert "a.exe" in err
    assert "b.exe" in err


def test_run_an_image_which_does_not_exist(bench, capsys):
    """ An image which cannot be stripped is a usage error. """
    assert _run(bench, str(bench.tmp_path / "no-such.exe")) == EXIT_USAGE
    assert "could not strip" in capsys.readouterr().err


def test_run_image_without_a_runner(bench, capsys):
    """ An image for a target without a runner reports that it never ran. """
    assert _run(bench,
                bench.image("c.exe", b"\x7fELF-c"),
                target="no/such/target") == EXIT_TRANSPORT
    err = capsys.readouterr().err
    assert "no image runner for target" in err
    assert "result status: unreached: no image runner for target" in err


def test_run_without_steps(capsys):
    """ Running nothing at all is a usage error. """
    assert clirun.clirun(["spectestrun"]) == EXIT_USAGE
    assert "no steps given" in capsys.readouterr().err


def test_run_a_sequence_in_the_order_of_the_command_line(bench, capsys):
    """ The step options run in the order they appear in. """
    assert _run(bench,
                extra=("--action", "/input/dummy:activate:name", "--image",
                       bench.image("f.exe", b"\x7fELF-f"), "--wait", "0",
                       "--action", "/input/dummy:deactivate")) == EXIT_OK
    err = capsys.readouterr().err
    order = [
        err.index("activate:name"),
        err.index("run: "),
        err.index("waited"),
        err.index("'deactivate'"),
    ]
    assert order == sorted(order)


def test_run_a_wait_step(bench, capsys):
    """ A wait step delays the sequence and reports what it waited. """
    begin = time.monotonic()
    assert _run(bench, extra=("--wait", "0.2")) == EXIT_OK
    assert time.monotonic() - begin >= 0.2
    assert "wait of 0.2 seconds -> waited" in capsys.readouterr().err


def test_run_a_wait_which_is_interrupted(bench, capsys, monkeypatch):
    """ A signal during a wait ends the sequence and skips the rest. """

    # The command installs the handler which stops it, so signal it for real
    # instead of reaching into the sequence.
    def _signal_the_command(_seconds):
        os.kill(os.getpid(), signal.SIGINT)

    monkeypatch.setattr(steps.time, "sleep", _signal_the_command)
    assert _run(bench,
                extra=("--wait", "3600", "--action",
                       "/input/dummy:status")) == EXIT_INTERRUPTED
    err = capsys.readouterr().err
    assert "stopped during the wait of 3600.0 seconds" in err
    assert "skipped: wait of 3600.0 seconds" in err
    assert "skipped: action 'status' for /input/dummy" in err


def test_run_stops_before_the_step_after_an_interrupt(bench, capsys):
    """
    An interrupt ends a sequence which has no wait to break out of.  A step
    which is doing work is never abandoned, so the stop takes effect before
    the step which follows the one that was running.
    """
    bench.servicer.interrupt_on_run = True
    assert _run(bench,
                extra=("--image", bench.image("n.exe",
                                              b"\x7fELF-n"), "--image",
                       bench.image("o.exe", b"\x7fELF-o"), "--action",
                       "/input/dummy:status")) == EXIT_INTERRUPTED
    err = capsys.readouterr().err

    # The first image ran to its end and nothing after it started.
    assert err.count("received result for") == 1
    assert "skipped: image" in err
    assert "skipped: action 'status' for /input/dummy" in err


def test_run_which_completes_despite_an_interrupt(bench, capsys):
    """
    An interrupt during the last step leaves nothing to skip, so the results
    are complete and the expected status still decides the exit code.
    """
    bench.servicer.interrupt_on_run = True
    assert _run(bench,
                extra=("--fail-on-status", "success", "--image",
                       bench.image("q.exe", b"\x7fELF-q"))) == EXIT_OK
    err = capsys.readouterr().err
    assert "skipped" not in err
    assert "result status: success" in err


def test_run_a_wait_which_is_no_number(bench, capsys):
    """ A wait which is no number is a usage error. """
    assert _run(bench, extra=("--wait", "soon")) == EXIT_USAGE
    assert "is no wait in seconds" in capsys.readouterr().err


def test_run_a_failed_action_skips_the_remaining_steps(bench, capsys):
    """ An action which fails stops the sequence and skips the rest. """
    assert _run(bench,
                extra=("--action", "/no/such/agent:status", "--image",
                       bench.image("g.exe", b"\x7fELF-g"))) == EXIT_ACTION
    err = capsys.readouterr().err
    assert "no such agent" in err
    assert "skipped: image" in err


def test_run_a_failed_action_can_be_told_to_continue(bench, capsys):
    """ A failure policy option applies to the step before it. """
    assert _run(bench,
                extra=("--action", "/no/such/agent:status",
                       "--continue-on-failure", "--image",
                       bench.image("h.exe", b"\x7fELF-h"))) == EXIT_ACTION
    err = capsys.readouterr().err
    assert "skipped" not in err
    assert "result status: success" in err


def test_run_a_successful_image_skips_nothing_when_told_to_stop(bench, capsys):
    """ The stop policy on a step which succeeded skips nothing after it. """
    assert _run(bench,
                extra=("--fail-on-status", "success", "--image",
                       bench.image("i.exe", b"\x7fELF-i"), "--stop-on-failure",
                       "--action", "/input/dummy:status")) == EXIT_OK
    assert "skipped" not in capsys.readouterr().err


def test_run_positionals_do_not_mix_with_step_options(bench, capsys):
    """ Positional images have no defined order among the step options. """
    assert _run(bench,
                bench.image("j.exe", b"\x7fELF-j"),
                extra=("--action", "/input/dummy:status")) == EXIT_USAGE
    assert "no defined order" in capsys.readouterr().err


def test_run_a_failure_policy_without_a_preceding_step(bench, capsys):
    """ A failure policy option before any step is a usage error. """
    assert _run(bench,
                extra=("--stop-on-failure", "--image",
                       bench.image("k.exe", b"\x7fELF-k"))) == EXIT_USAGE
    assert "--stop-on-failure has no preceding step" in capsys.readouterr().err


def test_run_a_malformed_action(bench, capsys):
    """ An action option which is no uid and action pair is a usage error. """
    assert _run(bench, extra=("--action", "just-a-uid")) == EXIT_USAGE
    assert "is no <uid>:<action>" in capsys.readouterr().err


def test_run_sends_the_digest_of_the_image(bench):
    """ The request carries the digest of the stripped image. """
    _run(bench, bench.image("l.exe", b"\x7fELF-l"))
    assert bench.servicer.run_requests[0].digest.startswith("sha256:")


def _io(bench, uid, *extra):
    return cliio.cliio(
        ["spectestio", "--server-address", bench.address, *extra, uid])


def test_input_stops_after_the_maximum_lines(bench, capsys):
    """ The input stream ends once the requested responses arrived. """
    assert _io(bench, STREAMING_UID, "--max-lines", "2") == EXIT_OK
    assert capsys.readouterr().out.count("tick") == 2


def test_input_stops_on_the_timeout(bench, capsys):
    """ The input stream ends once the deadline of the call expires. """
    assert _io(bench, SILENT_UID, "--timeout", "0.5") == EXIT_OK
    assert capsys.readouterr().out == ""


def test_input_of_an_unknown_agent(bench, capsys):
    """ An input stream which the server leaves empty ends at once. """
    assert _io(bench, "/no/such/agent", "--timeout", "5") == EXIT_OK
    assert capsys.readouterr().out == ""


def test_log_stops_after_the_maximum_lines(bench, capsys):
    """ The log stream ends once the requested responses arrived. """
    assert clilog.clilog([
        "spectestlog", "--server-address", bench.address, "--max-lines", "1",
        "--timeout", "20"
    ]) == EXIT_OK
    assert capsys.readouterr().out.count(LOG_LINE) == 1


def test_log_stops_on_the_timeout(bench, capsys):
    """ The log stream of a silent logger ends on the deadline. """
    assert clilog.clilog([
        "spectestlog", "--server-address", bench.address, "--timeout", "1.5",
        SILENT_LOGGER
    ]) == EXIT_OK
    assert capsys.readouterr().out == ""


def test_action_which_fails(bench, capsys):
    """ An action which the agent fails is reported and exits non-zero. """
    assert _action(bench, "/switch/lazy-0:activate:x") == EXIT_ACTION
    assert "the switch is broken" in capsys.readouterr().err


def test_run_fail_on_status(bench, capsys):
    """ A run status other than the expected one exits non-zero. """
    assert _run(bench,
                bench.image("d.exe", b"\x7fELF-d"),
                extra=("--fail-on-status", "failure")) == EXIT_STATUS
    assert "instead of 'failure'" in capsys.readouterr().err


def test_run_fail_on_status_which_matches(bench):
    """ The expected run status exits with zero. """
    assert _run(bench,
                bench.image("e.exe", b"\x7fELF-e"),
                extra=("--fail-on-status", "success")) == EXIT_OK


def test_a_server_which_is_not_reachable(capsys):
    """ A server which does not answer yields the transport exit code. """
    assert cliaction.cliaction([
        "spectestaction", "--server-address", DEAD_ADDRESS,
        "/input/dummy:status"
    ]) == EXIT_TRANSPORT
    assert "failed" in capsys.readouterr().err


def test_a_run_on_a_server_which_is_not_reachable(bench, capsys):
    """
    A sequence gives up if the server does not answer.  The bridge attempts
    such a request again, while this command has nothing to attempt again.
    """
    assert clirun.clirun([
        "spectestrun", "--server-address", DEAD_ADDRESS, "--nm", bench.nm,
        "--strip", bench.strip,
        bench.image("m.exe", b"\x7fELF-m")
    ]) == EXIT_TRANSPORT
    assert "UNAVAILABLE" in capsys.readouterr().err


def test_a_stream_of_a_server_which_stops(bench):
    """ A stream of a server which stopped yields the transport exit code. """
    bench.stop()
    assert cliio.cliio(
        ["spectestio", "--server-address", bench.address,
         STREAMING_UID]) == EXIT_TRANSPORT
