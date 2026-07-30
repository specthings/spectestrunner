# SPDX-License-Identifier: BSD-2-Clause
""" Prepare test executables for a test server. """

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

import hashlib
import subprocess
import tempfile

#: The symbol used to derive the breakpoints of an executable.
BREAKPOINT_SYMBOL = "bsp_reset"


class ImageError(RuntimeError):
    """ This error indicates that an executable could not be prepared. """


def get_symbols(exe_path: str, nm_path: str) -> dict[str, list[int]]:
    """
    Return the symbols of the executable using the nm tool.

    An executable without a symbol table has no symbols, while a tool which
    cannot be run at all is an error of the command line.
    """
    try:
        result = subprocess.run([nm_path, exe_path],
                                check=True,
                                capture_output=True,
                                text=True)
    except subprocess.CalledProcessError:
        return {}
    except OSError as err:
        raise ImageError(f"could not run '{nm_path}': {err}") from err
    symbols: dict[str, list[int]] = {}
    for line in result.stdout.split("\n"):
        try:
            address, _, name = line.rstrip("\r\n").split(" ", 2)
        except ValueError:
            pass
        else:
            try:
                value = int(address, 16)
            except ValueError:
                pass
            else:
                symbols.setdefault(name, []).append(value)
    return symbols


def get_breakpoints(exe_path: str, nm_path: str) -> list[int]:
    """ Return the breakpoints of the executable. """
    return get_symbols(exe_path, nm_path).get(BREAKPOINT_SYMBOL, [])


def strip_image(exe_path: str, strip_path: str) -> bytes:
    """ Return the executable stripped of its debug information. """
    with tempfile.NamedTemporaryFile() as tmp:
        try:
            subprocess.run([strip_path, "-g", "-o", tmp.name, exe_path],
                           check=True)
        except (subprocess.CalledProcessError, OSError) as err:
            raise ImageError(f"could not strip '{exe_path}': {err}") from err
        return tmp.read()


def get_digest(data: bytes) -> str:
    """ Return the digest of the image data. """
    return "sha256:" + hashlib.sha256(data).hexdigest()
