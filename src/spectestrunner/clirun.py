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
import subprocess
import sys
import tempfile

import grpc
from specitems import get_arguments

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


def _get_symbols(exe_path: str, nm_path: str) -> dict[str, list[int]]:
    """" Return the symbols of the executable using the nm tool. """
    try:
        result = subprocess.run([nm_path, exe_path],
                                check=True,
                                capture_output=True,
                                text=True)
    except subprocess.CalledProcessError:
        return {}
    symbols: dict[str, list[int]] = {}
    for line in result.stdout.split("\n"):
        try:
            address, _, name = line.rstrip("\r\n").split(" ", 2)
        except ValueError:
            pass
        else:
            try:
                symbols.setdefault(name, []).append(int(address, 16))
            except ValueError:
                pass
    return symbols


def clirun(argv: list[str] = sys.argv):
    """ Run images using gRPC. """
    args = _get_arguments(argv[1:])
    with grpc.insecure_channel(args.server_address) as channel:
        stub = GRPCServiceStub(channel)
        for image in args.images:
            breakpoints = _get_symbols(image, args.nm).get("bsp_reset", [])
            logging.info("send: %s", image)
            with tempfile.NamedTemporaryFile() as tmp:
                subprocess.run([args.strip, "-g", "-o", tmp.name, image],
                               check=True)
                data = tmp.read()

            result = stub.request_run_image(
                GRPCRunImageRequest(target_id=args.target,
                                    breakpoints=breakpoints,
                                    path=image,
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
