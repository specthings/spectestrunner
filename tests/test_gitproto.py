# SPDX-License-Identifier: BSD-2-Clause
""" Test the protocol encoding and decoding. """

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

import pytest

from spectestrunner import gitproto, steps

_IMAGE_STEP = {
    "kind": steps.STEP_IMAGE,
    "path": "build/ticker.exe",
    "file": "images/0000-ticker.exe",
    "digest": "sha256:00",
    "breakpoints": [4096],
}

_ACTION_STEP = {
    "kind": steps.STEP_ACTION,
    "uid": "/service/some-peer",
    "action": "activate:bc:600",
}


def test_request_round_trip():
    """ A request survives encoding and decoding. """
    request_steps = [_ACTION_STEP, _IMAGE_STEP]
    message = gitproto.encode_request("tester", "aarch64/zynqmp_apu", 180.0,
                                      request_steps)
    assert message.startswith(
        "spectest: request 2 steps for aarch64/zynqmp_apu\n\n")
    payload = gitproto.decode_request(message)
    assert payload["kind"] == gitproto.KIND_RUN_STEPS
    assert payload["target"] == "aarch64/zynqmp_apu"
    assert payload["steps"] == request_steps
    gitproto.check_run_steps_request(payload)


def test_single_step_subject():
    """ The subject of a one step request is not plural. """
    message = gitproto.encode_request("tester", "t", 1.0, [_IMAGE_STEP])
    assert message.startswith("spectest: request 1 step for t\n\n")


def test_trailers_do_not_break_the_block():
    """ Text after the block end is ignored. """
    message = gitproto.encode_request(
        "tester", "t", 1.0,
        [_IMAGE_STEP]) + "\nSigned-off-by: Someone <someone@example.com>\n"
    assert gitproto.decode_request(message)["target"] == "t"


@pytest.mark.parametrize("step, expected", [
    (_IMAGE_STEP, True),
    (_ACTION_STEP, False),
    ({
        **_IMAGE_STEP, "continue_on_failure": False
    }, False),
    ({
        **_ACTION_STEP, "continue_on_failure": True
    }, True),
])
def test_continue_on_failure(step, expected):
    """ The failure policy defaults to the step kind and can be overridden. """
    assert steps.continue_on_failure(step) is expected


@pytest.mark.parametrize("status, expected", [
    ("success", True),
    ("success: started, active as 'bc'", True),
    ("error: no such agent", False),
    ("skipped", False),
    ("", False),
])
def test_succeeded(status, expected):
    """ Only a status with the success sentinel counts as success. """
    assert steps.succeeded(status) is expected


def test_response_round_trip():
    """ A response survives encoding and decoding. """
    results = [{"path": "a", "file": "output/0000-a.out", "status": "OK"}]
    message = gitproto.encode_response("a" * 40, gitproto.STATUS_COMPLETED,
                                       results)
    payload = gitproto.decode_response(message)
    assert payload["request"] == "a" * 40
    assert payload["results"] == results
    assert "reason" not in payload


def test_rejected_response_carries_a_reason():
    """ A rejected response states why. """
    message = gitproto.encode_response("b" * 40, gitproto.STATUS_REJECTED, [],
                                       "unsupported kind 'action'")
    payload = gitproto.decode_response(message)
    assert payload["reason"] == "unsupported kind 'action'"


@pytest.mark.parametrize("message", [
    "no block at all",
    "s\n\n--- spectest-request ---\nversion: 1\n",
    "s\n\n--- spectest-request ---\n: : :\n--- end ---\n",
    "s\n\n--- spectest-request ---\njust a string\n--- end ---\n",
    "s\n\n--- spectest-request ---\nversion: 99\n--- end ---\n",
    "s\n\n--- spectest-request ---\nversion: 1\n--- end ---\n",
])
def test_malformed_requests(message):
    """ Malformed requests raise a protocol error. """
    with pytest.raises(gitproto.ProtocolError):
        gitproto.decode_request(message)


def _request(*steps):
    return {"target": "t", "timeout": 1.0, "steps": list(steps)}


@pytest.mark.parametrize("payload", [
    {},
    {
        "target": 1
    },
    {
        "target": "t"
    },
    {
        "target": "t",
        "timeout": 1.0
    },
    _request(),
    _request("nope"),
    _request({"path": "a"}),
    _request({
        "kind": "reboot",
        "path": "a"
    }),
    _request({
        "kind": steps.STEP_IMAGE,
        "path": "a"
    }),
    _request({
        "kind": steps.STEP_IMAGE,
        "path": "a",
        "file": "f",
        "digest": "d",
        "breakpoints": ["x"]
    }),
    _request({
        "kind": steps.STEP_ACTION,
        "uid": "/a"
    }),
    _request({
        "kind": steps.STEP_ACTION,
        "uid": "/a",
        "action": ""
    }),
    _request({
        **_IMAGE_STEP, "continue_on_failure": "yes"
    }),
])
def test_malformed_run_steps_requests(payload):
    """ Malformed run steps requests raise a protocol error. """
    with pytest.raises(gitproto.ProtocolError):
        gitproto.check_run_steps_request(payload)


@pytest.mark.parametrize("message", [
    "s\n\n--- spectest-response ---\nversion: 1\nstatus: bogus\n--- end ---\n",
    "s\n\n--- spectest-response ---\nversion: 1\nstatus: completed\n--- end ---\n",
])
def test_malformed_responses(message):
    """ Malformed responses raise a protocol error. """
    with pytest.raises(gitproto.ProtocolError):
        gitproto.decode_response(message)


@pytest.mark.parametrize("name, expected", [
    ("sebhub@example.com", "sebhub-example.com"),
    ("feature/branch", "feature-branch"),
    ("a..b", "a.b"),
    ("-lead-", "lead"),
    ("x.lock", "x"),
    ("with space", "with-space"),
    ("ca^ret~tilde:colon?star*", "ca-ret-tilde-colon-star"),
])
def test_sanitize_ref_component(name, expected):
    """ Reference name components are reduced to valid characters. """
    assert gitproto.sanitize_ref_component(name) == expected


def test_sanitize_ref_component_rejects_empty():
    """ A name without valid characters is an error. """
    with pytest.raises(ValueError):
        gitproto.sanitize_ref_component("///")


def test_reference_names():
    """ Reference names round trip through their components. """
    ref = gitproto.request_ref("tester", "c" * 40)
    assert gitproto.split_request_ref(ref) == ("tester", "c" * 40)
    assert gitproto.split_response_ref(gitproto.response_ref("c" *
                                                             40)) == "c" * 40
    assert gitproto.get_default_submitter()


@pytest.mark.parametrize("ref", [
    f"{gitproto.REQUESTS_PREFIX}/only",
    f"{gitproto.RESPONSES_PREFIX}/a/b",
    f"{gitproto.RESPONSES_PREFIX}/",
])
def test_malformed_reference_names(ref):
    """ Malformed reference names raise a protocol error. """
    with pytest.raises(gitproto.ProtocolError):
        if ref.startswith(gitproto.REQUESTS_PREFIX):
            gitproto.split_request_ref(ref)
        else:
            gitproto.split_response_ref(ref)
