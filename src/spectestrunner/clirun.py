# SPDX-License-Identifier: BSD-2-Clause
""" Run images on a test server through gRPC. """

# Copyright (C) 2024 embedded brains GmbH & Co. KG
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

import grpc
from specitems import get_arguments

from spectestrunner import image

# pylint: disable=no-name-in-module
from spectestrunner import GRPCRunImageRequest, GRPCServiceStub  # type: ignore


def _get_arguments(argv: list[str]) -> argparse.Namespace:

    def _add_arguments(parser):
        parser.add_argument("--target",
                            help="the target identifier",
                            default="/does/not/exist")
        parser.add_argument("--timeout",
                            help="the execution timeout",
                            type=float,
                            default=180.0)
        parser.add_argument("--server-address",
                            help="the server address",
                            default="localhost:50051")
        parser.add_argument("--nm",
                            help="the path to the nm tool",
                            default="nm")
        parser.add_argument("--strip",
                            help="the path to the strip tool",
                            default="strip")
        parser.add_argument("images", nargs='+')

    return get_arguments(argv,
                         description=clirun.__doc__,
                         add_arguments=(_add_arguments, ))


def clirun(argv: list[str] = sys.argv):
    """ Run images using gRPC. """
    args = _get_arguments(argv[1:])
    with grpc.insecure_channel(args.server_address) as channel:
        stub = GRPCServiceStub(channel)
        for exe_path in args.images:
            breakpoints = image.get_breakpoints(exe_path, args.nm)
            logging.info("send: %s", exe_path)
            data = image.strip_image(exe_path, args.strip)
            result = stub.request_run_image(
                GRPCRunImageRequest(target_id=args.target,
                                    breakpoints=breakpoints,
                                    path=exe_path,
                                    digest="digest",
                                    data=data,
                                    execution_timeout_in_seconds=args.timeout))
            logging.info("received result for: %s", result.path)
            logging.info("result status: %s", result.status)
            logging.info("load duration in seconds: %s",
                         result.load_duration_in_seconds)
            logging.info("execution duration in seconds: %s",
                         result.execution_duration_in_seconds)
            print(result.output.decode("latin-1"))
