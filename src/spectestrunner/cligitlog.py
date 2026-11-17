# SPDX-License-Identifier: BSD-2-Clause
""" Read the stored log records of a test server through a Git repository. """

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
import sys

from specitems import get_arguments

from spectestrunner import gitclient, gitproto, gitwire, logring
from spectestrunner.exitcodes import (EXIT_MISSING, EXIT_OK, EXIT_REJECTED,
                                      EXIT_TIMEOUT, EXIT_TRANSPORT)


def _get_arguments(argv: list[str]) -> argparse.Namespace:

    def _add_arguments(parser):
        parser.add_argument("--remote",
                            help="the Git remote used as transport",
                            required=True)
        parser.add_argument("--submitter",
                            help="the submitter identifier",
                            default=gitproto.get_default_submitter())
        parser.add_argument("--work-dir",
                            help="the local Git repository of the submitter",
                            default=None)
        parser.add_argument("--wait-timeout",
                            help="the timeout to wait for a response",
                            type=float,
                            default=3600.0)
        parser.add_argument("--poll-interval",
                            help="the response polling interval",
                            type=float,
                            default=10.0)
        logring.add_selection_arguments(parser)

    return get_arguments(argv,
                         description=cligitlog.__doc__,
                         add_arguments=(_add_arguments, ))


def _print_logs(logs: list[dict]) -> int:
    """ Print the records of each source and return the exit code. """
    status = EXIT_OK
    for entry in logs:
        source = entry.get("source")
        if "error" in entry:
            logging.error("%s: %s", source, entry["error"])
            status = EXIT_TRANSPORT
            continue
        logging.info("%s: %s records", source, entry.get("records"))
        if entry.get("omitted"):
            logging.warning("%s: the record limit omitted %s records", source,
                            entry["omitted"])
        print(f"--- {source} ---")
        sys.stdout.write(entry.get("output", b"").decode("utf-8", "replace"))
    return status


def cligitlog(argv: list[str] = sys.argv) -> int:
    """ Read the stored log records of a test server through a Git repository.
    """
    args = _get_arguments(argv[1:])
    try:
        with gitclient.repository(args.work_dir,
                                  prefix="spectestgitlog-") as repo:
            logs = gitclient.Client(repo, args.remote, args.submitter,
                                    args.wait_timeout,
                                    args.poll_interval).read_log(
                                        args.since, args.until,
                                        args.request_id, args.max_records)
    except gitclient.WaitTimeout as err:
        logging.error("%s", err)
        return EXIT_TIMEOUT
    except gitclient.Rejected as err:
        logging.error("%s", err)
        return EXIT_REJECTED
    except gitclient.RequestGone as err:
        logging.error("%s", err)
        return EXIT_MISSING
    except (gitproto.ProtocolError, gitwire.GitError) as err:
        logging.error("%s", err)
        return EXIT_TRANSPORT
    return _print_logs(logs)
