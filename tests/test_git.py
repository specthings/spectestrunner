# SPDX-License-Identifier: BSD-2-Clause
""" Test the Git mediated test server protocol. """

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
import os
import signal
import stat
import subprocess
import time

import grpc
import pytest

from spectestrunner import cligitbridge, cligitrun, gitproto, gitwire, image

_NM = """#!/bin/sh
echo "0000000000001000 T bsp_reset"
echo "0000000000002000 T main"
"""

_STRIP = """#!/bin/sh
# usage: strip -g -o OUTPUT INPUT
cp "$4" "$3"
"""


def _write_tool(path, text):
    with open(path, "w", encoding="utf-8") as out:
        out.write(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


class _Response:  # pylint: disable=too-few-public-methods
    """ Mimics a GRPCRunImageResponse. """

    def __init__(self, path, output, status):
        self.target_id = "aarch64/zynqmp_apu"
        self.path = path
        self.digest = "digest"
        self.output = output
        self.status = status
        self.load_duration_in_seconds = 1.5
        self.execution_duration_in_seconds = 2.5


class _Channel:  # pylint: disable=too-few-public-methods

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _RpcError(grpc.RpcError):

    def __init__(self, code):
        super().__init__()
        self._code = code

    def code(self):
        return self._code


class _ActionResponse:  # pylint: disable=too-few-public-methods
    """ Mimics a GRPCActionResponse. """

    def __init__(self, request, status):
        self.uid = request.uid
        self.action = request.action
        self.status = status


class _Stub:
    """ Answers run image and action requests with canned responses. """

    #: The error raised instead of answering, if any.
    error = None

    #: The requests received so far.
    requests = []

    #: The action status by action string, defaulting to success.
    action_status = {}

    #: The status reported for every image run.
    image_status = "success"

    #: The error raised by an image run only, if any.
    image_error = None

    def __init__(self, _channel):
        pass

    def request_run_image(self, request, timeout=None):
        # pylint: disable=unused-argument
        _Stub.requests.append(request)
        if _Stub.error is not None:
            raise _Stub.error
        if _Stub.image_error is not None:
            raise _Stub.image_error
        return _Response(request.path, b"output of " + request.data,
                         _Stub.image_status)

    def request_action(self, request, timeout=None):
        assert timeout is not None, "an action needs a deadline"
        _Stub.requests.append(request)
        if _Stub.error is not None:
            raise _Stub.error
        return _ActionResponse(
            request,
            _Stub.action_status.get(request.action,
                                    f"success: done {request.action}"))


@pytest.fixture(name="bench")
def _bench(tmp_path, monkeypatch):
    """ Provide a remote repository, fake tools and a fake gRPC service. """
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "--quiet",
                    str(remote)],
                   check=True)
    tools = tmp_path / "tools"
    tools.mkdir()
    _Stub.error = None
    _Stub.requests = []
    _Stub.action_status = {}
    _Stub.image_status = "success"
    _Stub.image_error = None
    monkeypatch.setattr(cligitbridge, "GRPCServiceStub", _Stub)
    monkeypatch.setattr(cligitbridge.grpc, "insecure_channel",
                        lambda _address: _Channel())

    class _Bench:  # pylint: disable=too-few-public-methods
        """ Holds the paths of one test bench. """

        def __init__(self):
            self.remote = str(remote)
            self.tmp_path = tmp_path
            self.nm = _write_tool(str(tools / "nm"), _NM)
            self.strip = _write_tool(str(tools / "strip"), _STRIP)

        def image(self, name, content):
            """ Create a fake executable and return its path. """
            path = tmp_path / name
            path.write_bytes(content)
            return str(path)

        def submit(self, *extra, images=()):
            """ Run the submitter and return its exit status. """
            return cligitrun.cligitrun([
                "spectestgitrun", "--remote", self.remote, "--submitter",
                "tester", "--nm", self.nm, "--strip", self.strip, "--target",
                "aarch64/zynqmp_apu", "--poll-interval", "0.01",
                "--wait-timeout", "10", *extra
            ] + list(images))

        def bridge(self, *extra):
            """ Run one bridge tick. """
            return cligitbridge.cligitbridge([
                "spectestgitbridge", "--remote", self.remote, "--work-dir",
                str(tmp_path / "bridge.git"), "--once", *extra
            ])

        def refs(self):
            """ Return the protocol references of the remote. """
            repo = gitwire.Repository(str(remote))
            return repo.remote_refs(str(remote), gitproto.REF_PATTERN)

        def hand(self):
            """ Return a repository for hand crafted commits. """
            repo = gitwire.Repository(str(tmp_path / "hand.git"))
            repo.init()
            return repo

        def push(self, commit, ref):
            """ Push a commit to a reference of the remote. """
            self.hand().push(self.remote, f"{commit}:{ref}")

        def deny_responses(self):
            """ Make the remote refuse every response reference. """
            _write_tool(
                str(remote / "hooks" / "pre-receive"), "#!/bin/sh\n"
                "while read -r old new ref; do\n"
                '  case "$ref" in ' + gitproto.RESPONSES_PREFIX + '/*)\n'
                '    echo "responses are refused" >&2; exit 1;;\n'
                "  esac\n"
                "done\n"
                "exit 0\n")

        def deny_deletions(self):
            """ Make the remote refuse every reference deletion. """
            _write_tool(
                str(remote / "hooks" / "pre-receive"), "#!/bin/sh\n"
                "while read -r old new ref; do\n"
                '  case "$new" in ' + "0" * 40 + ')\n'
                '    echo "deletions are refused" >&2; exit 1;;\n'
                "  esac\n"
                "done\n"
                "exit 0\n")

    return _Bench()


def test_round_trip(bench, capsys):
    """ A submitted request is answered and both references are removed. """
    exe = bench.image("ticker.exe", b"\x7fELF-ticker")
    assert bench.submit("--no-wait", images=[exe]) == cligitrun.EXIT_OK
    request_id = capsys.readouterr().out.strip()

    refs = bench.refs()
    assert gitproto.request_ref("tester", request_id) in refs
    assert gitproto.response_ref(request_id) not in refs

    assert bench.bridge() == 0
    assert gitproto.response_ref(request_id) in bench.refs()

    assert bench.submit("--collect", request_id) == cligitrun.EXIT_OK
    assert "output of \x7fELF-ticker" in capsys.readouterr().out
    assert not bench.refs()


def test_request_carries_breakpoints_and_digest(bench, capsys):
    """ The request reaches the server with breakpoints and a real digest. """
    exe = bench.image("ticker.exe", b"\x7fELF-ticker")
    bench.submit("--no-wait", images=[exe])
    capsys.readouterr()
    bench.bridge()
    assert len(_Stub.requests) == 1
    request = _Stub.requests[0]
    assert list(request.breakpoints) == [0x1000]
    assert request.digest == image.get_digest(b"\x7fELF-ticker")
    assert request.data == b"\x7fELF-ticker"


def test_multiple_images_in_one_commit(bench, capsys):
    """ One commit carries a batch and one response carries all results. """
    images = [
        bench.image("a.exe", b"aaa"),
        bench.image("b.exe", b"bbb"),
    ]
    bench.submit("--no-wait", images=images)
    request_id = capsys.readouterr().out.strip()
    bench.bridge()
    assert bench.submit("--collect", request_id) == cligitrun.EXIT_OK
    out = capsys.readouterr().out
    assert "output of aaa" in out
    assert "output of bbb" in out


def test_binary_output_is_byte_exact(bench, capsys):
    """ Output bytes survive the commit round trip unchanged. """
    exe = bench.image("t.exe", b"\x00\x01\x02\xff")
    bench.submit("--no-wait", images=[exe])
    request_id = capsys.readouterr().out.strip()
    bench.bridge()
    repo = gitwire.Repository(str(bench.tmp_path / "bridge.git"))
    commit = bench.refs()[gitproto.response_ref(request_id)]
    payload = gitproto.decode_response(repo.commit_message(commit))
    blobs = repo.tree_entries(commit)
    data = repo.blob(blobs[payload["results"][0]["file"]])
    assert data == b"output of \x00\x01\x02\xff"


def test_bridge_is_idempotent(bench, capsys):
    """ A second tick does not run an already answered request again. """
    exe = bench.image("ticker.exe", b"one")
    bench.submit("--no-wait", images=[exe])
    capsys.readouterr()
    bench.bridge()
    bench.bridge()
    assert len(_Stub.requests) == 1


def test_unsupported_kind_is_rejected(bench, capsys):
    """ An unknown request kind yields a rejected response. """
    repo = gitwire.Repository(str(bench.tmp_path / "hand.git"))
    repo.init()
    message = gitproto.encode_message("spectest: bogus",
                                      "--- spectest-request ---", {
                                          "version": gitproto.VERSION,
                                          "kind": "action",
                                      })
    commit = repo.commit_tree(repo.make_tree({}), message)
    repo.push(bench.remote,
              f"{commit}:{gitproto.request_ref('tester', commit)}")

    assert bench.bridge() == 0
    assert bench.submit("--collect", commit) == cligitrun.EXIT_REJECTED
    assert not _Stub.requests


def test_digest_mismatch_is_rejected(bench, capsys):
    """ A tampered image blob yields a rejected response. """
    repo = gitwire.Repository(str(bench.tmp_path / "hand.git"))
    repo.init()
    message = gitproto.encode_request(
        "tester", "aarch64/zynqmp_apu", 1.0,
        [{
            "kind": gitproto.STEP_IMAGE,
            "path": "t.exe",
            "file": "images/0000-t.exe",
            "digest": image.get_digest(b"expected"),
            "breakpoints": [],
        }])
    tree = repo.make_tree({
        "images": {
            "0000-t.exe": (gitwire.MODE_EXECUTABLE, repo.hash_object(b"other"))
        }
    })
    commit = repo.commit_tree(tree, message)
    repo.push(bench.remote,
              f"{commit}:{gitproto.request_ref('tester', commit)}")

    bench.bridge()
    assert bench.submit("--collect", commit) == cligitrun.EXIT_REJECTED
    assert not _Stub.requests


def test_transient_failure_retries_then_rejects(bench, capsys):
    """ A transient gRPC failure is retried up to the attempt limit. """
    exe = bench.image("ticker.exe", b"one")
    bench.submit("--no-wait", images=[exe])
    request_id = capsys.readouterr().out.strip()
    _Stub.error = _RpcError(grpc.StatusCode.UNAVAILABLE)

    bridge = cligitbridge.Bridge(
        _arguments(bench, max_attempts=3, response_retention=1e9))
    bridge.tick()
    assert gitproto.response_ref(request_id) not in bench.refs()
    bridge.tick()
    assert gitproto.response_ref(request_id) not in bench.refs()
    bridge.tick()
    assert gitproto.response_ref(request_id) in bench.refs()
    assert bench.submit("--collect", request_id) == cligitrun.EXIT_REJECTED
    assert len(_Stub.requests) == 3


def test_permanent_grpc_failure_fails_the_step(bench, capsys):
    """ A non-transient gRPC failure fails the step and is not retried. """
    exe = bench.image("ticker.exe", b"one")
    bench.submit("--no-wait", images=[exe])
    request_id = capsys.readouterr().out.strip()
    _Stub.error = _RpcError(grpc.StatusCode.INVALID_ARGUMENT)
    bench.bridge()
    assert len(_Stub.requests) == 1
    bench.bridge()
    assert len(_Stub.requests) == 1

    repo = gitwire.Repository(str(bench.tmp_path / "bridge.git"))
    commit = bench.refs()[gitproto.response_ref(request_id)]
    payload = gitproto.decode_response(repo.commit_message(commit))
    assert payload["status"] == gitproto.STATUS_COMPLETED
    assert "INVALID_ARGUMENT" in payload["results"][0]["status"]


def test_abandoned_request_is_reaped(bench, capsys):
    """ A request past the retention is removed even without a response. """
    exe = bench.image("ticker.exe", b"one")
    bench.submit("--no-wait", images=[exe])
    request_id = capsys.readouterr().out.strip()
    _Stub.error = _RpcError(grpc.StatusCode.UNAVAILABLE)
    bench.bridge("--response-retention", "0", "--max-attempts", "1000")
    assert gitproto.request_ref("tester", request_id) not in bench.refs()


def test_answered_pair_is_reaped(bench, capsys):
    """ An uncollected request and response pair is removed after the TTL. """
    exe = bench.image("ticker.exe", b"one")
    bench.submit("--no-wait", images=[exe])
    capsys.readouterr()
    bench.bridge()
    assert bench.refs()
    bench.bridge("--response-retention", "0")
    assert not bench.refs()


def test_wait_timeout(bench):
    """ Waiting without a bridge exits with the timeout status. """
    exe = bench.image("ticker.exe", b"one")
    assert bench.submit("--wait-timeout", "0.05",
                        images=[exe]) == cligitrun.EXIT_TIMEOUT


def test_fail_on_status(bench, capsys):
    """ The fail-on-status option turns an unexpected status into a failure. """
    exe = bench.image("ticker.exe", b"one")
    bench.submit("--no-wait", images=[exe])
    request_id = capsys.readouterr().out.strip()
    bench.bridge()
    assert bench.submit("--fail-on-status", "success", "--collect",
                        request_id) == cligitrun.EXIT_OK
    capsys.readouterr()

    bench.submit("--no-wait", images=[exe])
    request_id = capsys.readouterr().out.strip()
    bench.bridge()
    assert bench.submit("--fail-on-status", "FAILED", "--collect",
                        request_id) == cligitrun.EXIT_STATUS


def test_no_steps(bench):
    """ Submitting without steps fails. """
    assert bench.submit() == cligitrun.EXIT_GIT


_PEER = "/service/some-peer"


def _results(bench, request_id):
    """ Return the result records of the response of the request. """
    repo = gitwire.Repository(str(bench.tmp_path / "bridge.git"))
    commit = bench.refs()[gitproto.response_ref(request_id)]
    return gitproto.decode_response(repo.commit_message(commit))["results"]


def _sequence(bench, *extra):
    """ Submit the bus controller then remote terminal sequence. """
    return bench.submit(
        "--no-wait",
        "--action",
        f"{_PEER}:activate:bc:600",
        "--image",
        bench.image("bc.exe", b"bc"),
        *extra,
        "--action",
        f"{_PEER}:activate:rt-4",
        "--image",
        bench.image("rt.exe", b"rt"),
        "--action",
        f"{_PEER}:deactivate",
    )


def test_action_and_image_sequence(bench, capsys):
    """ Actions and images run in the order given on the command line. """
    assert _sequence(bench) == cligitrun.EXIT_OK
    request_id = capsys.readouterr().out.strip()
    bench.bridge()

    assert [
        request.action
        if hasattr(request, "action") else os.path.basename(request.path)
        for request in _Stub.requests
    ] == [
        "activate:bc:600", "bc.exe", "activate:rt-4", "rt.exe", "deactivate"
    ]
    results = _results(bench, request_id)
    assert [result["kind"] for result in results] == [
        gitproto.STEP_ACTION, gitproto.STEP_IMAGE, gitproto.STEP_ACTION,
        gitproto.STEP_IMAGE, gitproto.STEP_ACTION
    ]
    assert results[0]["uid"] == _PEER
    assert results[0]["status"] == "success: done activate:bc:600"
    assert "file" not in results[0]

    assert bench.submit("--collect", request_id) == cligitrun.EXIT_OK
    out = capsys.readouterr()
    assert "output of bc" in out.out
    assert "output of rt" in out.out
    assert f"action 'activate:bc:600' for {_PEER}" in out.err


def test_failed_action_skips_the_remaining_steps(bench, capsys):
    """ A failed action stops the sequence and the rest is reported. """
    _Stub.action_status = {"activate:rt-4": "error: no command for 'rt-4'"}
    _sequence(bench)
    request_id = capsys.readouterr().out.strip()
    bench.bridge()

    assert [
        request.action
        if hasattr(request, "action") else os.path.basename(request.path)
        for request in _Stub.requests
    ] == ["activate:bc:600", "bc.exe", "activate:rt-4"]
    results = _results(bench, request_id)
    assert [result["status"] for result in results][2:] == [
        "error: no command for 'rt-4'", gitproto.STATUS_SKIPPED,
        gitproto.STATUS_SKIPPED
    ]
    assert results[3]["path"] == bench.image("rt.exe", b"rt")
    assert results[4]["action"] == "deactivate"

    assert bench.submit("--collect", request_id) == cligitrun.EXIT_ACTION
    err = capsys.readouterr().err
    assert "skipped: image" in err
    assert f"skipped: action 'deactivate' for {_PEER}" in err


def test_failed_action_can_be_told_to_continue(bench, capsys):
    """ An action with the continue policy does not stop the sequence. """
    _Stub.action_status = {"activate:bc": "error: nope"}
    bench.submit("--no-wait", "--action", f"{_PEER}:activate:bc",
                 "--continue-on-failure", "--image",
                 bench.image("bc.exe", b"bc"))
    request_id = capsys.readouterr().out.strip()
    bench.bridge()

    results = _results(bench, request_id)
    assert [result["status"]
            for result in results] == ["error: nope", "success"]
    assert bench.submit("--collect", request_id) == cligitrun.EXIT_ACTION


def test_failed_image_does_not_stop_the_sequence(bench, capsys):
    """ An image which reports a failure is a result and not a precondition. """
    _Stub.image_status = "error: the test failed"
    bench.submit("--no-wait", "--image", bench.image("a.exe", b"a"), "--image",
                 bench.image("b.exe", b"b"))
    request_id = capsys.readouterr().out.strip()
    bench.bridge()

    assert len(_Stub.requests) == 2
    assert gitproto.STATUS_SKIPPED not in [
        result["status"] for result in _results(bench, request_id)
    ]
    assert bench.submit("--collect", request_id) == cligitrun.EXIT_OK


def test_image_can_be_told_to_stop_the_sequence(bench, capsys):
    """ An image with the stop policy skips what follows it. """
    _Stub.image_status = "error: the test failed"
    bench.submit("--no-wait", "--image", bench.image("a.exe", b"a"),
                 "--stop-on-failure", "--image", bench.image("b.exe", b"b"))
    request_id = capsys.readouterr().out.strip()
    bench.bridge()

    assert len(_Stub.requests) == 1
    results = _results(bench, request_id)
    assert results[1]["status"] == gitproto.STATUS_SKIPPED
    assert bench.submit("--collect", request_id) == cligitrun.EXIT_OK


def test_positionals_do_not_mix_with_step_options(bench, capsys):
    """ Positional images have no defined order among the step options. """
    assert bench.submit("--no-wait",
                        "--action",
                        f"{_PEER}:status",
                        images=[bench.image("a.exe",
                                            b"a")]) == cligitrun.EXIT_GIT
    assert "no defined order" in capsys.readouterr().err


def test_malformed_action_argument(bench, capsys):
    """ An action option which is no uid and action pair fails. """
    assert bench.submit("--no-wait", "--action",
                        "just-a-uid") == cligitrun.EXIT_GIT
    assert "is no <uid>:<action>" in capsys.readouterr().err


def test_a_rejected_request_runs_no_step(bench):
    """ A request which cannot run does not activate anything first. """
    repo = bench.hand()
    message = gitproto.encode_request(
        "tester", "aarch64/zynqmp_apu", 1.0,
        [{
            "kind": gitproto.STEP_ACTION,
            "uid": _PEER,
            "action": "activate:bc",
        }, {
            "kind": gitproto.STEP_IMAGE,
            "path": "t.exe",
            "file": "images/0001-t.exe",
            "digest": image.get_digest(b"expected"),
            "breakpoints": [],
        }])
    tree = repo.make_tree({
        "images": {
            "0001-t.exe": (gitwire.MODE_EXECUTABLE, repo.hash_object(b"other"))
        }
    })
    commit = repo.commit_tree(tree, message)
    bench.push(commit, gitproto.request_ref("tester", commit))

    bench.bridge()
    assert bench.submit("--collect", commit) == cligitrun.EXIT_REJECTED
    assert not _Stub.requests


def test_debug_logs_the_sequence_and_the_git_commands(bench, capsys):
    """ The debug level shows every step and every Git command. """
    _Stub.action_status = {"activate:rt-4": "error: no command for 'rt-4'"}
    _sequence(bench, "--log-level", "DEBUG")
    submitted = capsys.readouterr()
    request_id = submitted.out.strip()
    bench.bridge("--log-level", "DEBUG")
    text = submitted.err + capsys.readouterr().err

    # The submitter has to show what it built and where it went
    assert "git push" in text
    assert gitproto.request_ref("tester", request_id) in text
    assert "activate:bc:600" in text

    # The bridge has to show the outcome of every step, including the
    # skipped ones, and the request it decoded
    assert f"request {request_id} is 'run-steps' from 'tester'" in text
    assert "exit status 0" in text
    assert f"action 'activate:rt-4' for {_PEER}: status 'error:" in text
    assert f"skip step 4 of 5: action 'deactivate' for {_PEER}" in text
    assert "and digest sha256:" in text


def test_a_permanent_step_failure_keeps_the_earlier_results(bench, capsys):
    """ A step which fails permanently does not discard what already ran. """
    _Stub.image_error = _RpcError(grpc.StatusCode.INVALID_ARGUMENT)
    bench.submit("--no-wait", "--action", f"{_PEER}:activate:bc", "--image",
                 bench.image("bc.exe", b"bc"))
    request_id = capsys.readouterr().out.strip()
    bench.bridge()

    # The activation happened, so the response has to say so.  Rejecting
    # the request would leave the peer active with an empty transcript.
    results = _results(bench, request_id)
    assert results[0]["status"] == "success: done activate:bc"
    assert results[1]["kind"] == gitproto.STEP_IMAGE
    assert "INVALID_ARGUMENT" in results[1]["status"]
    assert bench.submit("--collect", request_id) == cligitrun.EXIT_OK


def test_failure_policy_needs_a_preceding_step(bench):
    """ A failure policy option before any step is a usage error. """
    with pytest.raises(SystemExit):
        bench.submit("--stop-on-failure", "--image",
                     bench.image("a.exe", b"a"))


def test_unknown_remote(bench, tmp_path):
    """ A broken remote yields the Git exit status. """
    exe = bench.image("ticker.exe", b"one")
    assert cligitrun.cligitrun([
        "spectestgitrun", "--remote",
        str(tmp_path / "missing.git"), "--strip", bench.strip, "--nm",
        bench.nm, "--no-wait", exe
    ]) == cligitrun.EXIT_GIT


def _arguments(bench, **overrides):
    values = {
        "remote": bench.remote,
        "work_dir": str(bench.tmp_path / "bridge.git"),
        "server_address": "localhost:50051",
        "poll_interval": 0.01,
        "response_retention": 1e9,
        "max_attempts": 3,
        "once": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.fixture(name="signals", autouse=True)
def _signals():
    """ Restore the signal handlers the bridge installs. """
    saved = [(num, signal.getsignal(num))
             for num in (signal.SIGINT, signal.SIGTERM)]
    yield
    for num, handler in saved:
        signal.signal(num, handler)


def test_work_dir_is_reused(bench, capsys, tmp_path):
    """ An explicit work directory is created and kept. """
    exe = bench.image("ticker.exe", b"one")
    work_dir = str(tmp_path / "submitter.git")
    assert bench.submit("--work-dir", work_dir, "--no-wait",
                        images=[exe]) == cligitrun.EXIT_OK
    request_id = capsys.readouterr().out.strip()
    assert os.path.isdir(os.path.join(work_dir, "objects"))
    assert gitwire.Repository(work_dir).commit_message(request_id)


def test_foreign_references_are_ignored(bench, capsys):
    """ References which are not well formed requests do not stop a tick. """
    exe = bench.image("ticker.exe", b"one")
    bench.submit("--no-wait", images=[exe])
    request_id = capsys.readouterr().out.strip()

    repo = bench.hand()
    other = repo.commit_tree(repo.make_tree({}), "unrelated\n")
    bench.push(other, f"{gitproto.REQUESTS_PREFIX}/without-an-identifier")
    bench.push(other, f"{gitproto.REQUESTS_PREFIX}/tester/not-the-commit")
    bench.push(other, f"{gitproto.RESPONSES_PREFIX}/nested/identifier")
    bench.push(other, f"{gitproto.REF_PREFIX}/neither-requests-nor-responses")

    assert bench.bridge() == 0
    assert gitproto.response_ref(request_id) in bench.refs()
    assert len(_Stub.requests) == 1


def test_missing_image_blob_is_rejected(bench):
    """ A request naming a file which is not in its commit is rejected. """
    repo = bench.hand()
    message = gitproto.encode_request(
        "tester", "aarch64/zynqmp_apu", 1.0,
        [{
            "kind": gitproto.STEP_IMAGE,
            "path": "t.exe",
            "file": "images/0000-missing.exe",
            "digest": image.get_digest(b"whatever"),
            "breakpoints": [],
        }])
    commit = repo.commit_tree(repo.make_tree({}), message)
    bench.push(commit, gitproto.request_ref("tester", commit))

    bench.bridge()
    assert bench.submit("--collect", commit) == cligitrun.EXIT_REJECTED
    assert not _Stub.requests


def test_refused_deletion_is_logged(bench, capsys):
    """ A failing reference deletion does not abort the bridge. """
    bench.submit("--no-wait", images=[bench.image("a.exe", b"a")])
    request_id = capsys.readouterr().out.strip()
    bench.deny_deletions()
    bridge = cligitbridge.Bridge(_arguments(bench))
    # pylint: disable=protected-access
    bridge._delete(gitproto.request_ref("tester", request_id))
    assert "could not delete" in capsys.readouterr().err
    assert gitproto.request_ref("tester", request_id) in bench.refs()


def test_stop_between_requests(bench, capsys):
    """ A tick which is asked to stop does not start the next request. """
    bench.submit("--no-wait", images=[bench.image("a.exe", b"a")])
    capsys.readouterr()
    bridge = cligitbridge.Bridge(_arguments(bench))
    bridge.stop = True
    bridge.tick()
    assert not _Stub.requests
    assert not [ref for ref in bench.refs() if "responses" in ref]


def test_run_logs_git_errors(bench, tmp_path, caplog):
    """ A broken remote is logged instead of crashing the loop. """
    bridge = cligitbridge.Bridge(
        _arguments(bench, remote=str(tmp_path / "missing.git")))
    bridge.run()
    assert "ls-remote" in caplog.text


def test_run_polls_until_signalled(bench, monkeypatch, capsys):
    """ The loop polls on its interval and stops on a signal. """
    bench.submit("--no-wait", images=[bench.image("a.exe", b"a")])
    capsys.readouterr()
    ticks = []

    def _tick(self):
        ticks.append(self)
        if len(ticks) > 1:
            os.kill(os.getpid(), signal.SIGTERM)

    monkeypatch.setattr(cligitbridge.Bridge, "tick", _tick)
    assert cligitbridge.cligitbridge([
        "spectestgitbridge", "--remote", bench.remote, "--work-dir",
        str(bench.tmp_path / "bridge.git"), "--poll-interval", "0.05"
    ]) == 0
    assert len(ticks) == 2


def test_malformed_response_is_reported(bench, capsys):
    """ A response which is not protocol conformant fails the collection. """
    repo = bench.hand()
    commit = repo.commit_tree(repo.make_tree({}), "not a response at all\n")
    bench.push(commit, gitproto.response_ref(commit))
    assert bench.submit("--collect", commit) == cligitrun.EXIT_GIT


def test_missing_output_blob_and_cleanup_failure(bench, capsys):
    """ A result without its output file prints nothing and cleans up. """
    repo = bench.hand()
    message = gitproto.encode_response(
        "a" * 40, gitproto.STATUS_COMPLETED,
        [{
            "kind": gitproto.STEP_IMAGE,
            "path": "t.exe",
            "file": "output/0000-gone.out",
            "status": "success",
            "load_duration_in_seconds": 1.0,
            "execution_duration_in_seconds": 2.0,
        }])
    commit = repo.commit_tree(repo.make_tree({}), message)
    bench.push(commit, gitproto.response_ref(commit))
    bench.deny_deletions()
    assert bench.submit("--collect", commit) == cligitrun.EXIT_OK
    assert "could not delete" in capsys.readouterr().err


def test_unpushable_response_is_retried(bench, capsys):
    """ A response which cannot be pushed counts as a failed attempt. """
    bench.submit("--no-wait", images=[bench.image("a.exe", b"a")])
    request_id = capsys.readouterr().out.strip()
    bench.deny_responses()

    bridge = cligitbridge.Bridge(_arguments(bench, max_attempts=1))
    bridge.tick()
    assert gitproto.response_ref(request_id) not in bench.refs()
    assert "could not answer" in capsys.readouterr().err
    assert len(_Stub.requests) == 1


def test_response_of_another_bridge_is_accepted(bench, capsys):
    """ Losing the race to answer is not a failure. """
    bench.submit("--no-wait", images=[bench.image("a.exe", b"a")])
    request_id = capsys.readouterr().out.strip()
    repo = bench.hand()
    other = repo.commit_tree(
        repo.make_tree({}),
        gitproto.encode_response(request_id, gitproto.STATUS_REJECTED, [],
                                 "answered by someone else"))
    bench.push(other, gitproto.response_ref(request_id))

    bridge = cligitbridge.Bridge(_arguments(bench))
    bridge.attempts[request_id] = 1
    # pylint: disable=protected-access
    assert bridge._publish(request_id, gitproto.STATUS_COMPLETED, [], None)
    assert request_id not in bridge.attempts
    assert "already answered" in capsys.readouterr().err
    assert bench.refs()[gitproto.response_ref(request_id)] == other


def test_unreadable_commit_time_is_zero(bench, caplog):
    """ A commit the bridge cannot read is treated as ancient. """
    bridge = cligitbridge.Bridge(_arguments(bench))
    # pylint: disable=protected-access
    assert bridge._age("0" * 40) == 0.0
    assert "could not read the time" in caplog.text


def test_vanished_request_fails_fast(bench):
    """ Collecting a request which does not exist does not wait. """
    started = time.monotonic()
    assert bench.submit("--collect", "0" * 40) == cligitrun.EXIT_MISSING
    assert time.monotonic() - started < 5.0


def test_answered_check_survives_a_broken_remote(bench, tmp_path):
    """ An unreachable remote does not claim the request is answered. """
    bridge = cligitbridge.Bridge(
        _arguments(bench, remote=str(tmp_path / "missing.git")))
    # pylint: disable=protected-access
    assert bridge._is_answered("0" * 40) is False
