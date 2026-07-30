# SPDX-License-Identifier: BSD-2-Clause
""" Provides the exit codes of the commands. """

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

#: The command did its work.  A run may still have reported a failure.
EXIT_OK = 0

#: The request was permanently refused.
EXIT_REJECTED = 1

#: The command line is not usable.  This is the code argparse itself uses
#: for a usage error, so the commands use it for their own ones as well.
EXIT_USAGE = 2

#: A transport operation failed.
EXIT_TRANSPORT = 3

#: A run reported a status other than the expected one.
EXIT_STATUS = 4

#: The request vanished before a response arrived.
EXIT_MISSING = 5

#: An action failed, so the steps after it did not run.
EXIT_ACTION = 6

#: The specification of the server is not valid.
EXIT_SPECIFICATION = 7

#: No response arrived before the wait timeout expired.
EXIT_TIMEOUT = 8
