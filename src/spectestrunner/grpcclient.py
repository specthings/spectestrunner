# SPDX-License-Identifier: BSD-2-Clause
""" Provides the gRPC client used by the commands. """

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

import logging
from typing import Any, Callable, Iterator

import grpc

from .exitcodes import EXIT_TRANSPORT

# pylint: disable=no-name-in-module
from .servicegrpc_pb2_grpc import GRPCServiceStub  # type: ignore

#: The prefix of the status of an action which succeeded.
ACTION_SUCCESS = "success"


def succeeded(status: str) -> bool:
    """ Return whether the status of an action indicates success. """
    return status.startswith(ACTION_SUCCESS)


def _code_of(err: grpc.RpcError) -> Any:
    """ Return the status code of the error, or None if it has none. """
    return err.code() if hasattr(err, "code") else None


def take_stream(responses: Any, max_lines: None | int = None) -> Iterator[Any]:
    """
    Yield the responses of the stream until a limit is reached.

    Stop once this many responses arrived and cancel the stream.  The
    deadline of the call ends the stream as well, which is how the timeout
    of a command works.  Without a limit the stream ends with the server.
    """
    count = 0
    try:
        for response in responses:
            yield response
            count += 1
            if max_lines is not None and count >= max_lines:
                break
    except grpc.RpcError as err:
        if _code_of(err) != grpc.StatusCode.DEADLINE_EXCEEDED:
            raise
    responses.cancel()


def run_with_service(address: str, work: Callable[[Any], int]) -> int:
    """
    Run the work with a service stub for the server at the address.

    Returns the exit code of the work, or the transport exit code if the
    server did not answer.
    """
    try:
        with grpc.insecure_channel(address) as channel:
            return work(GRPCServiceStub(channel))
    except grpc.RpcError as err:
        logging.error("the request to %s failed: %s", address, _code_of(err))
        return EXIT_TRANSPORT
