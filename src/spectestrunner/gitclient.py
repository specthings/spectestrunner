# SPDX-License-Identifier: BSD-2-Clause
""" Submit requests to a test server through a Git repository. """

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

import contextlib
import dataclasses
import logging
import os
import tempfile
import time
from typing import Any, Iterator, Optional

from spectestrunner import gitproto, gitwire


class RequestGone(RuntimeError):
    """ This error indicates that the request no longer exists. """


class WaitTimeout(RuntimeError):
    """ This error indicates that no response arrived in time. """


class Rejected(RuntimeError):
    """ This error indicates that the bridge rejected the request. """


@contextlib.contextmanager
def repository(work_dir: Optional[str] = None,
               prefix: str = "spectestgit-") -> Iterator[gitwire.Repository]:
    """
    Yield the local repository of the submitter.

    Without a work directory, the repository lives in a temporary directory
    which is removed on exit.
    """
    if work_dir is None:
        with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
            repo = gitwire.Repository(os.path.join(tmp, "repo.git"))
            repo.init()
            yield repo
    else:
        repo = gitwire.Repository(work_dir)
        repo.init()
        yield repo


@dataclasses.dataclass
class Client:
    """ Submits requests of one submitter to a test server through a remote.
    """
    repo: gitwire.Repository
    remote: str
    submitter: str
    wait_timeout: float = 3600.0
    poll_interval: float = 10.0

    def _push_request(self, tree: str, message: str) -> str:
        logging.debug("request message:\n%s", message)
        request_id = self.repo.commit_tree(tree, message)
        ref = gitproto.request_ref(self.submitter, request_id)
        self.repo.push(self.remote, f"{request_id}:{ref}")
        return request_id

    def submit_run_steps(self, target: str, timeout: float,
                         request_steps: list[dict[str, Any]],
                         data: dict[int, bytes]) -> str:
        """
        Push a run steps request and return its identifier.

        The data holds the image of every image step by its index in the
        sequence.  Each image step gains the name of its file in the commit.
        """
        entries: dict[str, Any] = {}
        for index, content in data.items():
            file = gitproto.image_file(
                index, os.path.basename(request_steps[index]["path"]))
            request_steps[index]["file"] = file
            entries.setdefault(
                gitproto.IMAGE_DIRECTORY,
                {})[os.path.basename(file)] = (gitwire.MODE_EXECUTABLE,
                                               self.repo.hash_object(content))
        message = gitproto.encode_request(self.submitter, target, timeout,
                                          request_steps)
        return self._push_request(self.repo.make_tree(entries), message)

    def submit_describe_target(self, target: str) -> str:
        """ Push a describe target request and return its identifier. """
        message = gitproto.encode_describe_target_request(
            self.submitter, target)
        return self._push_request(self.repo.make_tree({}), message)

    def wait_for_response(self, request_id: str) -> Optional[str]:
        """
        Return the response commit identifier, or None on timeout.  Raise a
        request gone error if the remote has neither a request nor a
        response reference, since then no response can ever arrive.
        """
        ref = gitproto.response_ref(request_id)
        pattern = gitproto.request_ref_pattern(request_id)
        deadline = time.monotonic() + self.wait_timeout
        polls = 0
        while True:
            refs = self.repo.remote_refs(self.remote, pattern, ref)
            polls += 1
            if ref in refs:
                logging.debug("response %s after %d polls", refs[ref], polls)
                self.repo.fetch(self.remote, f"+{ref}:{ref}")
                return refs[ref]
            if not refs:
                raise RequestGone(
                    f"the remote has neither a request nor a response "
                    f"reference for {request_id}")
            if time.monotonic() >= deadline:
                return None
            logging.debug("poll %d: still waiting for %s, %.0fs left", polls,
                          ref, deadline - time.monotonic())
            time.sleep(
                min(self.poll_interval, max(0.0, deadline - time.monotonic())))

    def read_response(self, commit: str) -> dict[str, Any]:
        """
        Return the payload of the response commit.

        The output of a run lives in a blob of the response commit.  Each
        result which names an output file which exists carries the output
        bytes.
        """
        message = self.repo.commit_message(commit)
        logging.debug("response message:\n%s", message)
        payload = gitproto.decode_response(message)
        blobs = self.repo.tree_entries(commit)
        for result in payload["results"]:
            object_id = blobs.get(result.get("file", ""))
            if object_id is not None:
                result["output"] = self.repo.blob(object_id)
        return payload

    def cleanup(self, request_id: str) -> None:
        """
        Delete the request references of the remote first, then the
        response reference.  A request without a response is withdrawn this
        way.
        """
        response = gitproto.response_ref(request_id)
        refs = self.repo.remote_refs(self.remote,
                                     gitproto.request_ref_pattern(request_id),
                                     response)
        for ref in sorted(refs, key=lambda name: name == response):
            try:
                self.repo.delete_remote_ref(self.remote, ref)
            except gitwire.GitError as err:
                logging.warning("could not delete %s: %s", ref, err)

    def _complete(self, request_id: str) -> dict[str, Any]:
        commit = self.wait_for_response(request_id)
        if commit is None:
            self.cleanup(request_id)
            raise WaitTimeout(f"no response for {request_id} within "
                              f"{self.wait_timeout} seconds")
        payload = self.read_response(commit)
        self.cleanup(request_id)
        if payload["status"] == gitproto.STATUS_REJECTED:
            raise Rejected(f"the request {request_id} was rejected: "
                           f"{payload.get('reason', 'no reason given')}")
        return payload

    def run_steps(self, target: str, timeout: float,
                  request_steps: list[dict[str, Any]],
                  data: dict[int, bytes]) -> list[dict[str, Any]]:
        """
        Run the steps through the remote and return the results.

        A request without a response in time is withdrawn.  Raise a wait
        timeout error in this case, and a rejected error if the bridge
        rejected the request.
        """
        return self._complete(
            self.submit_run_steps(target, timeout, request_steps,
                                  data))["results"]

    def describe_target(self, target: str) -> str:
        """
        Return the description of the target through the remote.

        The errors are the ones of run_steps().
        """
        payload = self._complete(self.submit_describe_target(target))
        description = payload.get("description")
        if not isinstance(description, str):
            raise gitproto.ProtocolError(
                "the response has no 'description' string")
        return description
