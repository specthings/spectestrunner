# SPDX-License-Identifier: BSD-2-Clause
""" Test the store of the recent log records. """

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

import argparse
import logging

import pytest

from spectestrunner import logring


def _emit(ring, message, created=0.0, name="some"):
    record = logging.LogRecord(name, logging.INFO, __file__, 1, message, None,
                               None)
    if created:
        record.created = created
    ring.handle(record)


def _messages(selection):
    return [line.rsplit(" ", 1)[1] for line in selection.lines]


def test_a_full_ring_drops_the_oldest_records():
    """ A new record drops the oldest records until the ring fits again. """
    ring = logring.LogRing()
    _emit(ring, "probe")
    line_length = len(ring.select().lines[0])
    ring = logring.LogRing(capacity=3 * line_length)
    for message in ("aaaaa", "bbbbb", "ccccc", "ddddd"):
        _emit(ring, message)
    assert _messages(ring.select()) == ["bbbbb", "ccccc", "ddddd"]


def test_a_long_record_is_truncated():
    """ A record longer than the line limit loses its end. """
    ring = logring.LogRing(line_limit=64)
    _emit(ring, "x" * 100)
    line = ring.select().lines[0]
    assert len(line) == 64
    assert line.endswith(" [truncated]")


def test_a_record_carries_the_tracked_requests():
    """ A record carries each request tracked at its time. """
    ring = logring.LogRing()
    _emit(ring, "none")
    with ring.track("abc123"):
        with ring.track("abc123"):
            _emit(ring, "inner")
        with ring.track("def456"):
            _emit(ring, "both")
        _emit(ring, "outer")
    with ring.track(""):
        _emit(ring, "after")
    assert _messages(
        ring.select(request_id="abc")) == ["inner", "both", "outer"]
    assert _messages(ring.select(request_id="def456")) == ["both"]
    assert len(ring.select().lines) == 5


def test_a_selection_by_time_logger_and_count():
    """ The selection takes the time range, the logger and the limit. """
    ring = logring.LogRing()
    _emit(ring, "one", 10.0)
    _emit(ring, "two", 20.0, "some.child")
    _emit(ring, "three", 30.0, "someone")
    _emit(ring, "four", 40.0)
    assert _messages(ring.select(begin=20.0, end=30.0)) == ["two", "three"]
    assert _messages(ring.select(logger_name="some")) == ["one", "two", "four"]
    selection = ring.select(max_records=3)
    assert _messages(selection) == ["one", "two", "three"]
    assert selection.omitted == 1
    assert ring.select(max_records=4).omitted == 0


def test_a_record_which_cannot_be_formatted(capsys):
    """ A record whose arguments do not fit is reported and not stored. """
    ring = logring.LogRing()
    record = logging.LogRecord("some", logging.INFO, __file__, 1, "%d",
                               ("x", ), None)
    ring.handle(record)
    assert not ring.select().lines
    assert "TypeError" in capsys.readouterr().err


def test_parse_time():
    """ A time without a zone is a local time. """
    assert logring.parse_time("1970-01-01T00:00:10+00:00") == 10.0
    assert logring.parse_time("2026-11-17T01:00:00") == pytest.approx(
        logring.parse_time("2026-11-17T01:00:00+00:00"), abs=86400.0)


def test_selection_arguments():
    """ The options of a selection convert the times. """
    parser = argparse.ArgumentParser()
    logring.add_selection_arguments(parser)
    args = parser.parse_args(["--since", "1970-01-01T00:00:01+00:00"])
    assert args.since == 1.0
    assert not args.until
    assert not args.request_id
    assert not args.max_records


class _Context:  # pylint: disable=too-few-public-methods

    def __init__(self, metadata):
        self._metadata = metadata

    def invocation_metadata(self):
        return self._metadata


def test_request_id_metadata():
    """ The request identifier travels as metadata of a call. """
    assert not logring.request_id_metadata("")
    metadata = logring.request_id_metadata("abc")
    assert logring.request_id_of(_Context((("other", "x"), ) +
                                          metadata)) == "abc"
    assert not logring.request_id_of(_Context((("other", "x"), )))
