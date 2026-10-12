import sqlite3

import pytest

from a4w_core.models import ChangeSet, DocumentChange, OperationChange, ReviewResult, SpecVersion
from a4w_detection.storage.sqlite_store import SqliteDetectionStore


@pytest.fixture
def store(tmp_path):
    return SqliteDetectionStore(tmp_path / "detection.db")


def version(v, commit="c", ops=1):
    return SpecVersion("wf", v, commit, 10, ops, "2026-10-12T00:00:00Z")


def changeset(cs_id, frm, to):
    return ChangeSet(
        change_set_id=cs_id, target_id="wf", from_version=frm, to_version=to,
        created_at="2026-10-12T00:00:00Z",
        operations=(
            OperationChange("GET", "/a", "a", "added", after={"method": "GET", "path": "/a"}),
            OperationChange("GET", "/b", "b", "modified", before={"x": 1}, after={"x": 2},
                            changed_fields=("x",), doc_only=False),
        ),
        document_changes=(DocumentChange("info", "modified"),),
        review=ReviewResult(True, ("OPERATION_CHANGED",)),
        text_diff="--- a\n+++ b\n",
    )


def test_empty_store(store):
    assert store.current("wf") is None
    assert store.get_changeset("wf", "cs-x") is None
    assert store.find_changeset("wf", None, "v1") is None


def test_record_sets_current_and_round_trips_changeset(store):
    cs = changeset("cs-1", None, "v1")
    store.record(version("v1", "c1"), "c1", cs, "2026-10-12T00:00:01Z")
    cur = store.current("wf")
    assert (cur.version, cur.git_commit) == ("v1", "c1")
    assert store.get_changeset("wf", "cs-1") == cs
    assert store.find_changeset("wf", None, "v1") == cs


def test_get_changeset_of_other_target_is_none(store):
    store.record(version("v1"), "c", changeset("cs-1", None, "v1"), "t")
    assert store.get_changeset("other", "cs-1") is None


def test_current_commit_follows_latest_record_even_if_version_seen_before(store):
    store.record(version("A", "c1"), "c1", changeset("cs-1", None, "A"), "t1")
    store.record(version("B", "c2"), "c2", changeset("cs-2", "A", "B"), "t2")
    store.record(version("A", "c1"), "c3", changeset("cs-3", "B", "A"), "t3")
    cur = store.current("wf")
    assert (cur.version, cur.git_commit) == ("A", "c3")
    assert cur.first_seen_at == "2026-10-12T00:00:00Z"


def test_duplicate_first_changeset_is_rejected(store):
    store.record(version("v1"), "c", changeset("cs-1", None, "v1"), "t")
    with pytest.raises(Exception):
        store.record(version("v1"), "c", changeset("cs-other", None, "v1"), "t")


def test_record_is_atomic(store, tmp_path):
    store.record(version("v1", "c1"), "c1", changeset("cs-1", None, "v1"), "t")
    bad = changeset("cs-1", "v1", "v2")  # 같은 change_set_id → 실패
    with pytest.raises(Exception):
        store.record(version("v2", "c2"), "c2", bad, "t")
    assert store.current("wf").version == "v1"
    con = sqlite3.connect(tmp_path / "detection.db")
    assert con.execute("select count(*) from spec_versions").fetchone()[0] == 1


def test_schema_version_is_set(tmp_path):
    SqliteDetectionStore(tmp_path / "d.db")
    con = sqlite3.connect(tmp_path / "d.db")
    assert con.execute("pragma user_version").fetchone()[0] == 1


def test_reopen_keeps_data(tmp_path):
    SqliteDetectionStore(tmp_path / "d.db").record(version("v1"), "c", changeset("cs-1", None, "v1"), "t")
    assert SqliteDetectionStore(tmp_path / "d.db").current("wf").version == "v1"
