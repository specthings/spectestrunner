# SPDX-License-Identifier: BSD-2-Clause
""" Test the Git plumbing wrappers. """

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

import os
import time

import pytest

from spectestrunner import gitwire


@pytest.fixture(name="repo")
def _repo(tmp_path):
    repository = gitwire.Repository(str(tmp_path / "repo.git"))
    repository.init()
    repository.init()
    return repository


def test_blob_round_trip(repo):
    """ Binary data survives the object database. """
    data = b"\x00\x01\x02\xff"
    assert repo.blob(repo.hash_object(data)) == data


def test_nested_tree(repo):
    """ Nested entries become nested trees. """
    tree = repo.make_tree({
        "top": (gitwire.MODE_FILE, repo.hash_object(b"top")),
        "dir": {
            "nested": (gitwire.MODE_EXECUTABLE, repo.hash_object(b"nested")),
        },
    })
    commit = repo.commit_tree(tree, "subject\n")
    entries = repo.tree_entries(commit)
    assert sorted(entries) == ["dir/nested", "top"]
    assert repo.blob(entries["dir/nested"]) == b"nested"


def test_tree_entries_skip_gitlinks(repo):
    """ A submodule entry is not mistaken for a file. """
    blob = repo.hash_object(b"data")
    empty = repo.make_tree({})
    gitlink = repo.commit_tree(empty, "submodule\n")
    tree = repo.run(["mktree"],
                    data=f"{gitwire.MODE_FILE} blob {blob}\tfile\n"
                    f"160000 commit {gitlink}\tsub\n").strip()
    commit = repo.commit_tree(tree, "subject\n")
    assert list(repo.tree_entries(commit)) == ["file"]


def test_commit_metadata(repo):
    """ The message and the committer time are read back. """
    commit = repo.commit_tree(repo.make_tree({}), "subject\n\nbody\n")
    assert repo.commit_message(commit) == "subject\n\nbody\n\n"
    assert abs(repo.commit_time(commit) - time.time()) < 60


def test_parentless_commits(repo):
    """ Commits have no parents, so nothing accumulates. """
    commit = repo.commit_tree(repo.make_tree({}), "subject\n")
    assert repo.text(["rev-list", "--count", commit]).strip() == "1"


def test_local_and_remote_refs(repo, tmp_path):
    """ References are listed the same way locally and remotely. """
    remote = gitwire.Repository(str(tmp_path / "remote.git"))
    remote.init()
    commit = repo.commit_tree(repo.make_tree({}), "subject\n")
    repo.push(remote.git_dir, f"{commit}:refs/spectest/requests/a/{commit}")
    assert repo.remote_refs(remote.git_dir, "refs/spectest/*") == {
        f"refs/spectest/requests/a/{commit}": commit
    }
    assert not repo.local_refs("refs/spectest")

    repo.fetch(remote.git_dir, "+refs/spectest/*:refs/spectest/*", prune=True)
    assert repo.local_refs("refs/spectest") == {
        f"refs/spectest/requests/a/{commit}": commit
    }

    remote.delete_remote_ref(remote.git_dir,
                             f"refs/spectest/requests/a/{commit}")
    assert not remote.remote_refs(remote.git_dir, "refs/spectest/*")


def test_identity_is_not_overridden(repo, monkeypatch):
    """ An environment identity wins over the built in default. """
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Someone")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "someone@example.com")
    commit = repo.commit_tree(repo.make_tree({}), "subject\n")
    assert repo.text(["show", "-s", "--format=%cn <%ce>",
                      commit]).strip() == "Someone <someone@example.com>"


def test_failed_command_reports_stderr(repo):
    """ A failing Git command raises with the reason. """
    with pytest.raises(gitwire.GitError) as info:
        repo.blob("0" * 40)
    assert "cat-file" in str(info.value)


def test_init_does_not_touch_an_existing_repository(repo):
    """ A second init keeps the objects of the first. """
    object_id = repo.hash_object(b"data")
    repo.init()
    assert repo.blob(object_id) == b"data"
    assert os.path.isdir(os.path.join(repo.git_dir, "objects"))


def test_a_work_directory_which_is_not_ours(tmp_path):
    """
    A directory which holds something else is no repository of the command.

    A clone of the remote would otherwise gain the files of a bare repository
    beside its own, and the command would work in that one, which knows none
    of the remotes of the clone.
    """
    clone = tmp_path / "clone"
    (clone / ".git").mkdir(parents=True)
    repo = gitwire.Repository(str(clone))
    with pytest.raises(gitwire.GitError, match="path of its own"):
        repo.init()
    assert sorted(item.name for item in clone.iterdir()) == [".git"]


def test_an_empty_work_directory_is_ours(tmp_path):
    """ An empty directory becomes the bare repository of the command. """
    empty = tmp_path / "empty"
    empty.mkdir()
    repo = gitwire.Repository(str(empty))
    repo.init()
    repo.init()
    assert (empty / "objects").is_dir()
