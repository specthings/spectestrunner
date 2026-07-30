# SPDX-License-Identifier: BSD-2-Clause
""" Encode and decode the Git mediated test server protocol. """

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

import getpass
import re
import socket
from typing import Any, Optional

import yaml

#: The protocol version of the embedded YAML documents.
VERSION = 1

#: The request kind which runs a step sequence on a target.
KIND_RUN_STEPS = "run-steps"

#: The step kind which runs an image on the target.
STEP_IMAGE = "image"

#: The step kind which requests an action of an agent.
STEP_ACTION = "action"

#: The result status of a step which was not reached.
STATUS_SKIPPED = "skipped"

#: The prefix of the action status of a successful action.
ACTION_SUCCESS = "success"

#: The response status of a completed request.
STATUS_COMPLETED = "completed"

#: The response status of a permanently refused request.
STATUS_REJECTED = "rejected"

#: The reference name space of the protocol.
REF_PREFIX = "refs/spectest"

#: The reference name space of the requests.
REQUESTS_PREFIX = f"{REF_PREFIX}/requests"

#: The reference name space of the responses.
RESPONSES_PREFIX = f"{REF_PREFIX}/responses"

#: The reference pattern which matches everything of the protocol.
REF_PATTERN = f"{REF_PREFIX}/*"

#: The directory of the images within a request commit.
IMAGE_DIRECTORY = "images"

#: The directory of the outputs within a response commit.
OUTPUT_DIRECTORY = "output"

_REQUEST_BEGIN = "--- spectest-request ---"

_RESPONSE_BEGIN = "--- spectest-response ---"

_BLOCK_END = "--- end ---"

_INVALID_REF_CHARACTERS = re.compile(r"[^A-Za-z0-9._-]")


class ProtocolError(ValueError):
    """ This error indicates a permanently malformed protocol message. """


def sanitize_ref_component(name: str) -> str:
    """ Return the name reduced to a valid reference name component. """
    value = _INVALID_REF_CHARACTERS.sub("-", name)
    value = re.sub(r"\.{2,}", ".", value)
    while value.endswith(".lock"):
        value = value[:-len(".lock")]
    value = value.strip("-.")
    if not value:
        raise ValueError(f"'{name}' has no valid reference name characters")
    return value


def get_default_submitter() -> str:
    """ Return the default submitter identifier of this host. """
    return sanitize_ref_component(
        f"{getpass.getuser()}-at-{socket.gethostname().split('.')[0]}")


def request_ref(submitter: str, request_id: str) -> str:
    """ Return the reference name of the request. """
    return f"{REQUESTS_PREFIX}/{submitter}/{request_id}"


def request_ref_pattern(request_id: str) -> str:
    """ Return a pattern matching the request reference of any submitter. """
    return f"{REQUESTS_PREFIX}/*/{request_id}"


def response_ref(request_id: str) -> str:
    """ Return the reference name of the response. """
    return f"{RESPONSES_PREFIX}/{request_id}"


def split_request_ref(ref: str) -> tuple[str, str]:
    """ Return the submitter and request identifier of the reference. """
    rest = ref[len(REQUESTS_PREFIX) + 1:]
    submitter, _, request_id = rest.rpartition("/")
    if not submitter or not request_id:
        raise ProtocolError(f"'{ref}' is not a request reference")
    return submitter, request_id


def split_response_ref(ref: str) -> str:
    """ Return the request identifier of the response reference. """
    request_id = ref[len(RESPONSES_PREFIX) + 1:]
    if not request_id or "/" in request_id:
        raise ProtocolError(f"'{ref}' is not a response reference")
    return request_id


def image_file(index: int, exe_path: str) -> str:
    """ Return the path of the image within the request commit. """
    name = sanitize_ref_component(exe_path.replace("/", "-"))
    return f"{IMAGE_DIRECTORY}/{index:04d}-{name}"


def output_file(index: int, exe_path: str) -> str:
    """ Return the path of the output within the response commit. """
    name = sanitize_ref_component(exe_path.replace("/", "-"))
    return f"{OUTPUT_DIRECTORY}/{index:04d}-{name}.out"


def encode_message(subject: str, marker: str, payload: dict[str, Any]) -> str:
    """ Return a commit message with the payload embedded as YAML. """
    document = yaml.safe_dump(payload,
                              default_flow_style=False,
                              sort_keys=False)
    return f"{subject}\n\n{marker}\n{document}{_BLOCK_END}\n"


def decode_message(message: str, marker: str) -> dict[str, Any]:
    """ Return the YAML payload embedded in the commit message. """
    lines = message.splitlines()
    try:
        begin = lines.index(marker)
    except ValueError:
        # pylint: disable=raise-missing-from
        raise ProtocolError(f"the commit message has no '{marker}' block")
    try:
        end = lines.index(_BLOCK_END, begin + 1)
    except ValueError:
        # pylint: disable=raise-missing-from
        raise ProtocolError(f"the '{marker}' block is not terminated")
    try:
        payload = yaml.safe_load("\n".join(lines[begin + 1:end]))
    except yaml.YAMLError as err:
        raise ProtocolError(
            f"the '{marker}' block is no valid YAML: {err}") from err
    if not isinstance(payload, dict):
        raise ProtocolError(f"the '{marker}' block is no YAML mapping")
    version = payload.get("version")
    if version != VERSION:
        raise ProtocolError(f"unsupported protocol version '{version}'")
    return payload


def encode_request(submitter: str, target: str, timeout: float,
                   steps: list[dict[str, Any]]) -> str:
    """
    Return the commit message of a run steps request.  The submitter is part
    of the payload so that two submitters cannot derive the same request
    identifier from an otherwise identical request.
    """
    count = len(steps)
    plural = "" if count == 1 else "s"
    subject = f"spectest: request {count} step{plural} for {target}"
    return encode_message(
        subject, _REQUEST_BEGIN, {
            "version": VERSION,
            "kind": KIND_RUN_STEPS,
            "submitter": submitter,
            "target": target,
            "timeout": timeout,
            "steps": steps,
        })


def decode_request(message: str) -> dict[str, Any]:
    """ Return the request payload of the commit message. """
    payload = decode_message(message, _REQUEST_BEGIN)
    if not isinstance(payload.get("kind"), str):
        raise ProtocolError("the request has no 'kind'")
    return payload


def _check_image_step(step: dict[str, Any]) -> None:
    for key in ("path", "file", "digest"):
        if not isinstance(step.get(key), str):
            raise ProtocolError(f"an image step has no '{key}' string")
    breakpoints = step.get("breakpoints", [])
    if not isinstance(breakpoints, list) or not all(
            isinstance(item, int) for item in breakpoints):
        raise ProtocolError("an image step has no 'breakpoints' integer list")


def _check_action_step(step: dict[str, Any]) -> None:
    # The action string stays opaque here.  The test server owns the action
    # grammar and is the only place which parses it.
    for key in ("uid", "action"):
        if not isinstance(step.get(key), str) or not step[key]:
            raise ProtocolError(f"an action step has no '{key}' string")


def check_run_steps_request(payload: dict[str, Any]) -> None:
    """ Raise a protocol error if the run steps request is malformed. """
    if not isinstance(payload.get("target"), str):
        raise ProtocolError("the request has no 'target' string")
    if not isinstance(payload.get("timeout"), (int, float)):
        raise ProtocolError("the request has no 'timeout' number")
    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ProtocolError("the request has no non-empty 'steps' list")
    for step in steps:
        if not isinstance(step, dict):
            raise ProtocolError("a step is no YAML mapping")
        kind = step.get("kind")
        if kind == STEP_IMAGE:
            _check_image_step(step)
        elif kind == STEP_ACTION:
            _check_action_step(step)
        else:
            raise ProtocolError(f"a step has the unsupported kind '{kind}'")
        if not isinstance(step.get("continue_on_failure", False), bool):
            raise ProtocolError("a step has no 'continue_on_failure' boolean")


def continue_on_failure(step: dict[str, Any]) -> bool:
    """
    Return whether the sequence continues after this step failed.

    A failed image step produced the result the request asked for, so the
    remaining steps still run.  A failed action step falsified the
    precondition of everything after it, so the sequence stops.
    """
    return bool(step.get("continue_on_failure",
                         step.get("kind") == STEP_IMAGE))


def succeeded(status: str) -> bool:
    """ Return whether the result status of a step indicates success. """
    return status.startswith(ACTION_SUCCESS)


def encode_response(request_id: str,
                    status: str,
                    results: list[dict[str, Any]],
                    reason: Optional[str] = None) -> str:
    """ Return the commit message of a response. """
    subject = f"spectest: {status} response for {request_id[:12]}"
    payload: dict[str, Any] = {
        "version": VERSION,
        "kind": KIND_RUN_STEPS,
        "request": request_id,
        "status": status,
    }
    if reason is not None:
        payload["reason"] = reason
    payload["results"] = results
    return encode_message(subject, _RESPONSE_BEGIN, payload)


def decode_response(message: str) -> dict[str, Any]:
    """ Return the response payload of the commit message. """
    payload = decode_message(message, _RESPONSE_BEGIN)
    if payload.get("status") not in (STATUS_COMPLETED, STATUS_REJECTED):
        raise ProtocolError("the response has no valid 'status'")
    if not isinstance(payload.get("results"), list):
        raise ProtocolError("the response has no 'results' list")
    return payload
