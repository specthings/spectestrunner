# SPDX-License-Identifier: BSD-2-Clause
""" Provides the fixtures shared by the tests. """

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
import os
import signal

import coverage
import pytest


def pytest_configure(config):  # pylint: disable=unused-argument
    """
    Make the coverage data file absolute.

    Coverage writes the data of a process relative to its working directory.
    Tests change the working directory, so a relative data file would be
    written to a directory which is removed after the test.
    """
    if coverage.Coverage.current() is not None:
        os.environ["COVERAGE_FILE"] = os.path.abspath(
            os.environ.get("COVERAGE_FILE", ".coverage"))


@pytest.fixture(name="log_handlers", autouse=True)
def _log_handlers():
    """
    Remove the logging handlers which the commands install.

    A command logs through the module level functions, which configure the
    root logger with a handler for the captured stream of the test.  A later
    test would write to this stream after the test which captured it is over.
    """
    root = logging.getLogger()
    saved = list(root.handlers)
    level = root.level
    yield
    for handler in list(root.handlers):
        if handler not in saved:
            root.removeHandler(handler)
    root.setLevel(level)


@pytest.fixture(name="signals", autouse=True)
def _signals():
    """ Restore the signal handlers installed by the commands. """
    saved = [(num, signal.getsignal(num))
             for num in (signal.SIGINT, signal.SIGTERM)]
    yield
    for num, handler in saved:
        signal.signal(num, handler)
