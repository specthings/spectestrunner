# SPDX-License-Identifier: BSD-2-Clause
""" Request a target description from a test server through gRPC. """

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
import sys
import grpc

from specitems import get_arguments

# pylint: disable=no-name-in-module
from spectestrunner import GRPCDescribeTargetRequest, GRPCServiceStub


def _get_arguments(argv: list[str]) -> argparse.Namespace:

    def _add_arguments(parser):
        parser.add_argument("--server-address",
                            help="the server address",
                            default="localhost:50051")
        parser.add_argument("target",
                            metavar="TARGET",
                            help="the target identifier",
                            nargs=1)

    return get_arguments(argv,
                         description=clidescribetarget.__doc__,
                         add_arguments=(_add_arguments, ))


def clidescribetarget(argv: list[str] = sys.argv):
    """ Request a target description from a test server through gRPC. """
    args = _get_arguments(argv[1:])
    with grpc.insecure_channel(args.server_address) as channel:
        stub = GRPCServiceStub(channel)
        response = stub.request_describe_target(
            GRPCDescribeTargetRequest(target_id=args.target[0]))
        print(response.description)
