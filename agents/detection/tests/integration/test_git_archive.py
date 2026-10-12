import subprocess

import pytest

from a4w_core.errors import StorageError
from a4w_detection.storage.git_archive import GitSpecArchive


@pytest.fixture
def archive(tmp_path):
    return GitSpecArchive(tmp_path, file_name="openapi.yaml")


def git_log(tmp_path, target):
    repo = tmp_path / "targets" / target / "specs"
    return subprocess.run(["git", "-C", str(repo), "log", "--format=%s"], capture_output=True, text=True, check=True).stdout.split("\n")[:-1]


def test_commit_and_read_back(archive, tmp_path):
    c1 = archive.commit("wf", b"v1\n", "h1")
    assert archive.read("wf", c1) == b"v1\n"
    assert git_log(tmp_path, "wf") == ["spec h1"]


def test_new_content_new_commit_old_still_readable(archive):
    c1 = archive.commit("wf", b"v1\n", "h1")
    c2 = archive.commit("wf", b"v2\n", "h2")
    assert c1 != c2
    assert archive.read("wf", c1) == b"v1\n" and archive.read("wf", c2) == b"v2\n"


def test_same_content_returns_head_without_new_commit(archive, tmp_path):
    c1 = archive.commit("wf", b"v1\n", "h1")
    assert archive.commit("wf", b"v1\n", "h1") == c1
    assert git_log(tmp_path, "wf") == ["spec h1"]


def test_reverted_content_gets_a_new_commit(archive, tmp_path):
    archive.commit("wf", b"A\n", "a")
    archive.commit("wf", b"B\n", "b")
    c3 = archive.commit("wf", b"A\n", "a")
    assert archive.read("wf", c3) == b"A\n"
    assert git_log(tmp_path, "wf") == ["spec a", "spec b", "spec a"]


def test_targets_are_separate_repos(archive, tmp_path):
    archive.commit("one", b"1\n", "h1")
    archive.commit("two", b"2\n", "h2")
    assert git_log(tmp_path, "one") == ["spec h1"]
    assert git_log(tmp_path, "two") == ["spec h2"]


def test_bytes_are_kept_exactly(archive):
    raw = "한글: 값\r\nno-newline-at-end".encode()
    c = archive.commit("wf", raw, "h")
    assert archive.read("wf", c) == raw


def test_unknown_commit_is_storage_error(archive):
    archive.commit("wf", b"v1\n", "h1")
    with pytest.raises(StorageError):
        archive.read("wf", "0" * 40)
