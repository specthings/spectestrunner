# SPDX-License-Identifier: BSD-2-Clause
""" Provides a store of the recent log records of a process. """

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
import collections
import contextlib
import dataclasses
import datetime
import logging
import threading
from typing import Any, Iterator

#: The gRPC metadata key which carries the request identifier of a call.
REQUEST_ID_METADATA = "spectest-request-id"

#: The default capacity of a ring in characters.
DEFAULT_CAPACITY = 16 * 1024 * 1024

#: The default length limit of one stored line in characters.
DEFAULT_LINE_LIMIT = 16 * 1024

_TRUNCATED = " [truncated]"


@dataclasses.dataclass(frozen=True)
class Selection:
    """
    Holds the stored lines which a selection matched.  The omitted count
    tells how many matching lines the record limit cut off at the end.
    """
    lines: list[str]
    omitted: int


class _Formatter(logging.Formatter):

    def formatTime(self,
                   record: logging.LogRecord,
                   datefmt: str | None = None) -> str:
        return datetime.datetime.fromtimestamp(
            record.created).astimezone().isoformat(timespec="milliseconds")


def parse_time(text: str) -> float:
    """
    Return the POSIX time of the ISO 8601 date and time.  A value without a
    time zone is a local time.
    """
    return datetime.datetime.fromisoformat(text).timestamp()


def add_selection_arguments(parser: argparse.ArgumentParser) -> None:
    """ Add the options which select stored log records. """
    parser.add_argument("--since",
                        help="select the records from this ISO 8601 time on",
                        type=parse_time,
                        default=0.0)
    parser.add_argument("--until",
                        help="select the records up to this ISO 8601 time",
                        type=parse_time,
                        default=0.0)
    parser.add_argument("--request-id",
                        help="select the records of the request whose "
                        "identifier starts with this",
                        default="")
    parser.add_argument("--max-records",
                        help="select at most this many records",
                        type=int,
                        default=0)


def request_id_metadata(request_id: str) -> tuple[tuple[str, str], ...]:
    """ Return the gRPC metadata of a call on behalf of the request. """
    if not request_id:
        return ()
    return ((REQUEST_ID_METADATA, request_id), )


def request_id_of(context: Any) -> str:
    """ Return the request identifier of the metadata of a gRPC call. """
    for key, value in context.invocation_metadata():
        if key == REQUEST_ID_METADATA:
            return value
    return ""


class LogRing(logging.Handler):
    """
    Stores the formatted lines of the most recent log records.

    The ring holds at most the capacity in characters.  A new line drops the
    oldest lines until the ring fits again.  A line longer than the line
    limit is truncated, so that one record cannot empty the ring.

    Each line carries the identifiers of the requests which are tracked at
    the time of its record.  A record of a thread which serves another
    request at the same time therefore carries both identifiers.
    """

    def __init__(self,
                 capacity: int = DEFAULT_CAPACITY,
                 line_limit: int = DEFAULT_LINE_LIMIT) -> None:
        super().__init__()
        self.setFormatter(
            _Formatter(fmt="%(asctime)s %(levelname)-8s %(message)s"))
        self._capacity = capacity
        self._line_limit = line_limit
        self._ring_lock = threading.Lock()
        self._size = 0
        self._lines: collections.deque[tuple[float, str, frozenset[str],
                                             str]] = collections.deque()
        self._active: collections.Counter[str] = collections.Counter()

    @contextlib.contextmanager
    def track(self, request_id: str) -> Iterator[None]:
        """ Tag the records of the context with the request identifier. """
        if not request_id:
            yield
            return
        with self._ring_lock:
            self._active[request_id] += 1
        try:
            yield
        finally:
            with self._ring_lock:
                self._active[request_id] -= 1
                if not self._active[request_id]:
                    del self._active[request_id]

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:  # pylint: disable=broad-exception-caught
            self.handleError(record)
            return
        if len(line) > self._line_limit:
            line = line[:self._line_limit - len(_TRUNCATED)] + _TRUNCATED
        with self._ring_lock:
            self._lines.append(
                (record.created, record.name, frozenset(self._active), line))
            self._size += len(line)
            while self._size > self._capacity:
                self._size -= len(self._lines.popleft()[3])

    def select(self,
               begin: float = 0.0,
               end: float = 0.0,
               request_id: str = "",
               max_records: int = 0,
               logger_name: str = "") -> Selection:
        """
        Return the stored lines in the time range of the request.

        A begin or an end of zero leaves that side of the range open.  A
        request identifier selects the lines whose request identifiers start
        with it.  A logger name selects the lines of that logger and of its
        descendants.  A positive record limit keeps the oldest lines.
        """
        with self._ring_lock:
            stored = list(self._lines)
        lines = [
            line for created, name, request_ids, line in stored
            if (not begin or created >= begin) and (
                not end or created <= end) and (not request_id or any(
                    item.startswith(request_id) for item in request_ids)) and (
                        not logger_name or name == logger_name
                        or name.startswith(f"{logger_name}."))
        ]
        if 0 < max_records < len(lines):
            return Selection(lines[:max_records], len(lines) - max_records)
        return Selection(lines, 0)
