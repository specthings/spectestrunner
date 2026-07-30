# SPDX-License-Identifier: BSD-2-Clause
""" Test the image preparation. """

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
import os
import stat

import pytest

from spectestrunner import image


def _tool(path, text):
    with open(path, "w", encoding="utf-8") as out:
        out.write(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return str(path)


@pytest.fixture(name="exe")
def _exe(tmp_path):
    path = tmp_path / "ticker.exe"
    path.write_bytes(b"\x7fELF-ticker")
    return str(path)


def test_symbols(tmp_path, exe):
    """ The nm output becomes a symbol to addresses map. """
    nm = _tool(
        tmp_path / "nm", "#!/bin/sh\n"
        'echo "0000000000001000 T bsp_reset"\n'
        'echo "0000000000002000 T bsp_reset"\n'
        'echo "0000000000003000 T main"\n')
    assert image.get_symbols(exe, nm) == {
        "bsp_reset": [0x1000, 0x2000],
        "main": [0x3000],
    }
    assert image.get_breakpoints(exe, nm) == [0x1000, 0x2000]


def test_failing_nm_yields_no_symbols(tmp_path, exe):
    """ A failing nm tool is not an error. """
    nm = _tool(tmp_path / "nm", "#!/bin/sh\nexit 1\n")
    assert image.get_symbols(exe, nm) == {}
    assert image.get_breakpoints(exe, nm) == []


def test_unparsable_nm_lines_are_skipped(tmp_path, exe):
    """ Lines without an address or without three fields are ignored. """
    nm = _tool(
        tmp_path / "nm", "#!/bin/sh\n"
        'echo "zzzzzzzz T not_an_address"\n'
        'echo "too few"\n'
        'echo "0000000000001000 T bsp_reset"\n')
    assert image.get_symbols(exe, nm) == {"bsp_reset": [0x1000]}


def test_an_nm_which_cannot_be_run(tmp_path, exe):
    """ An nm tool which does not exist is an error of its own. """
    with pytest.raises(image.ImageError, match="could not run"):
        image.get_symbols(exe, str(tmp_path / "no-such-nm"))


def test_strip_image(tmp_path, exe):
    """ The stripped image is read back from the temporary file. """
    strip = _tool(tmp_path / "strip", '#!/bin/sh\ncp "$4" "$3"\n')
    assert image.strip_image(exe, strip) == b"\x7fELF-ticker"


def test_a_strip_which_fails(tmp_path, exe):
    """ A strip tool which reports a failure is an error. """
    strip = _tool(tmp_path / "strip", "#!/bin/sh\nexit 1\n")
    with pytest.raises(image.ImageError, match="could not strip"):
        image.strip_image(exe, strip)


def test_a_strip_which_cannot_be_run(tmp_path, exe):
    """ A strip tool which does not exist is an error. """
    with pytest.raises(image.ImageError, match="could not strip"):
        image.strip_image(exe, str(tmp_path / "no-such-strip"))


def test_digest():
    """ The digest names its algorithm. """
    assert image.get_digest(
        b"data") == "sha256:" + hashlib.sha256(b"data").hexdigest()


def test_a_strip_which_removes_its_output(tmp_path, exe):
    """
    A strip which removes the output it was given is reported.

    The tool does that when it does not recognize the format of the input, and
    cleaning the output up afterwards hid why the strip failed.
    """
    strip = _tool(tmp_path / "strip", '#!/bin/sh\nrm -f "$3"\nexit 1\n')
    with pytest.raises(image.ImageError, match="could not strip"):
        image.strip_image(exe, strip)


def test_a_strip_which_writes_no_output(tmp_path, exe):
    """ A strip which reports success without an output is reported. """
    strip = _tool(tmp_path / "strip", "#!/bin/sh\nexit 0\n")
    with pytest.raises(image.ImageError, match="could not strip"):
        image.strip_image(exe, strip)
