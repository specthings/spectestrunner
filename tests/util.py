# SPDX-License-Identifier: BSD-2-Clause
""" Provides methods used by tests. """

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

import os
import stat

_NM = """#!/bin/sh
echo "0000000000001000 T bsp_reset"
echo "0000000000002000 T main"
"""

_STRIP = """#!/bin/sh
# usage: strip -g -o OUTPUT INPUT
cp "$4" "$3"
"""


def write_tool(path: str, text: str) -> str:
    """ Write the shell script and make it executable. """
    with open(path, "w", encoding="utf-8") as out:
        out.write(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


def write_image_tools(directory) -> tuple[str, str]:
    """ Write stand-ins for the nm and strip tools and return their paths. """
    os.makedirs(str(directory), exist_ok=True)
    return (write_tool(os.path.join(str(directory), "nm"), _NM),
            write_tool(os.path.join(str(directory), "strip"), _STRIP))
