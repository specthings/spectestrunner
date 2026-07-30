# SPDX-License-Identifier: BSD-2-Clause
""" Access a Git repository through the plumbing commands. """

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
import subprocess
import time
from typing import Optional, Union

#: The mode of a regular file tree entry.
MODE_FILE = "100644"

#: The mode of an executable file tree entry.
MODE_EXECUTABLE = "100755"

_IDENTITY = {
    "GIT_AUTHOR_NAME": "spectestrunner",
    "GIT_AUTHOR_EMAIL": "spectestrunner@localhost",
    "GIT_COMMITTER_NAME": "spectestrunner",
    "GIT_COMMITTER_EMAIL": "spectestrunner@localhost",
}


class GitError(RuntimeError):
    """ This error indicates that a Git command failed. """


class Repository:
    """ Provides access to the object database of a Git repository. """

    def __init__(self, git_dir: str):
        self.git_dir = git_dir

    def run(self,
            args: list[str],
            data: Optional[Union[str, bytes]] = None,
            binary: bool = False) -> Union[str, bytes]:
        """ Run a Git command and return its standard output. """
        if isinstance(data, str):
            data = data.encode("utf-8")
        env = dict(os.environ)
        for name, value in _IDENTITY.items():
            env.setdefault(name, value)
        logging.debug("git %s: run in %s with %d input bytes", " ".join(args),
                      self.git_dir, len(data or b""))
        begin = time.monotonic()
        result = subprocess.run(["git", "--git-dir", self.git_dir] + args,
                                input=data,
                                capture_output=True,
                                env=env,
                                check=False)
        reason = result.stderr.decode("utf-8", "replace").strip()

        # The output is not logged.  It carries blobs and can be a whole
        # test executable.
        logging.debug(
            "git %s: exit status %d after %.3fs with %d output "
            "bytes", args[0], result.returncode,
            time.monotonic() - begin, len(result.stdout))
        if reason:
            logging.debug("git %s: %s", args[0], reason)
        if result.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed with exit status "
                           f"{result.returncode}: {reason}")
        if binary:
            return result.stdout
        return result.stdout.decode("utf-8")

    def text(self, args: list[str]) -> str:
        """ Run a Git command and return its standard output as text. """
        output = self.run(args)
        assert isinstance(output, str)
        return output

    def init(self) -> None:
        """
        Create the bare repository if it does not exist.

        The repository belongs to this command, so a directory which holds
        something else is refused.  A clone of the remote would otherwise gain
        the files of a bare repository beside its own, and the command would
        then work in that new repository, which knows none of the remotes of
        the clone.
        """
        if os.path.isdir(os.path.join(self.git_dir, "objects")):
            return
        if os.path.isdir(self.git_dir) and os.listdir(self.git_dir):
            raise GitError(
                f"'{self.git_dir}' holds something other than the bare "
                f"repository of this command, so give a path of its own")
        logging.debug("create the repository %s", self.git_dir)
        os.makedirs(self.git_dir, exist_ok=True)
        subprocess.run(["git", "init", "--bare", "--quiet", self.git_dir],
                       check=True)

    def hash_object(self, data: bytes) -> str:
        """ Write the data as a blob and return its object identifier. """
        output = self.run(["hash-object", "-w", "--stdin"], data=data)
        assert isinstance(output, str)
        return output.strip()

    def make_tree(self, entries: dict) -> str:
        """
        Write a tree and return its object identifier.  The entries map a name
        to either a nested entries dictionary or a mode and blob identifier
        tuple.
        """
        lines = []
        for name in sorted(entries):
            entry = entries[name]
            if isinstance(entry, dict):
                lines.append(f"040000 tree {self.make_tree(entry)}\t{name}")
            else:
                mode, object_id = entry
                lines.append(f"{mode} blob {object_id}\t{name}")
        output = self.run(["mktree"],
                          data="".join(f"{line}\n" for line in lines))
        assert isinstance(output, str)
        return output.strip()

    def commit_tree(self, tree: str, message: str) -> str:
        """ Write a parentless commit and return its object identifier. """
        output = self.run(["commit-tree", tree, "-F", "-"], data=message)
        assert isinstance(output, str)
        return output.strip()

    def commit_message(self, commit: str) -> str:
        """ Return the message of the commit. """
        return self.text(["show", "-s", "--format=%B", commit])

    def commit_time(self, commit: str) -> int:
        """ Return the committer time of the commit in seconds since epoch. """
        return int(self.text(["show", "-s", "--format=%ct", commit]).strip())

    def blob(self, object_id: str) -> bytes:
        """ Return the content of the blob. """
        data = self.run(["cat-file", "blob", object_id], binary=True)
        assert isinstance(data, bytes)
        return data

    def tree_entries(self, commit: str) -> dict[str, str]:
        """ Return a map of path to blob identifier for the commit. """
        entries = {}
        for record in self.text(["ls-tree", "-r", "-z", commit]).split("\0"):
            if not record:
                continue
            info, _, path = record.partition("\t")
            _, kind, object_id = info.split(" ", 2)
            if kind == "blob":
                entries[path] = object_id
        return entries

    def local_refs(self, pattern: str) -> dict[str, str]:
        """ Return a map of reference name to object identifier. """
        refs = {}
        for line in self.text(
            ["for-each-ref", "--format=%(objectname) %(refname)",
             pattern]).splitlines():
            object_id, _, name = line.partition(" ")
            refs[name] = object_id
        return refs

    def remote_refs(self, remote: str, *patterns: str) -> dict[str, str]:
        """ Return a map of remote reference name to object identifier. """
        refs = {}
        for line in self.text(["ls-remote", remote, *patterns]).splitlines():
            object_id, _, name = line.partition("\t")
            refs[name] = object_id
        return refs

    def fetch(self, remote: str, refspec: str, prune: bool = False) -> None:
        """ Fetch the reference specification from the remote. """
        args = ["fetch", "--quiet", "--no-tags"]
        if prune:
            args.append("--prune")
        self.run(args + [remote, refspec])

    def push(self, remote: str, refspec: str) -> None:
        """ Push the reference specification to the remote. """
        self.run(["push", "--quiet", remote, refspec])

    def create_remote_ref(self, remote: str, ref: str, commit: str) -> None:
        """
        Create the reference on the remote.  Fail if it already exists.  Git
        enforces fast forward updates only below refs/heads and refs/tags, so
        an ordinary push would silently overwrite the reference.
        """
        self.run([
            "push", "--quiet", f"--force-with-lease={ref}:", remote,
            f"{commit}:{ref}"
        ])

    def delete_remote_ref(self, remote: str, ref: str) -> None:
        """ Delete the reference on the remote. """
        self.push(remote, f":{ref}")
