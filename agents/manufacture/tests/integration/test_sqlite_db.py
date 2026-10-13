"""manufacture SQLite (Phase 2 설계 13.3절)."""

from __future__ import annotations

import pytest

from a4w_core.errors import StorageError
from a4w_manufacture.storage.sqlite_db import OperationRecord, SqliteManufactureDb, TargetState


@pytest.fixture
def db(tmp_path):
    return SqliteManufactureDb(tmp_path / "manufacture.db")


def op(method="POST", path="/pages/{pageId}/comments", state="active", **over):
    rec = OperationRecord(target_id="wf", method=method, path=path, operation_id="comments.create",
                          fragment={"operationId": "comments.create"}, fragment_sha256="a" * 64,
                          structure_sha256="b" * 64, spec_version="s1", selection="tool", reason=None,
                          tool_name="add_comment", state=state, last_error=None, updated_at="t")
    return OperationRecord(**{**rec.__dict__, **over})


def test_state_roundtrip(db):
    assert db.state("wf") is None
    st = TargetState("wf", last_applied_spec_version="s1", last_change_set_id="cs-1",
                     head_tool_version="tv-000001-aaaaaaaa", current_tool_version="tv-000001-aaaaaaaa", updated_at="t")
    with db.transaction() as tx:
        tx.save_state(st)
    assert db.state("wf") == st


def test_operations_replace_in_one_transaction(db):
    with db.transaction() as tx:
        tx.save_operations("wf", [op(), op(method="GET", path="/pages/{id}", operation_id="pages.get")])
    ops = db.operations("wf")
    assert set(ops) == {("POST", "/pages/{pageId}/comments"), ("GET", "/pages/{id}")}
    assert ops[("GET", "/pages/{id}")].operation_id == "pages.get"
    with db.transaction() as tx:
        tx.save_operations("wf", [op(state="failed", last_error=[{"code": "X"}])])
    assert list(db.operations("wf")) == [("POST", "/pages/{pageId}/comments")]
    assert db.operations("wf")[("POST", "/pages/{pageId}/comments")].last_error == [{"code": "X"}]


def test_transaction_rolls_back(db):
    with pytest.raises(RuntimeError):
        with db.transaction() as tx:
            tx.save_operations("wf", [op()])
            raise RuntimeError("x")
    assert db.operations("wf") == {}


def test_applies(db):
    db.create_apply({"applyId": "ap-1", "targetId": "wf", "changeSetId": "cs-1", "status": "running", "startedAt": "t"})
    assert db.get_apply("ap-1")["status"] == "running"
    assert db.find_done_apply("wf", "cs-1") is None
    db.finish_apply("ap-1", status="applied", error_code=None, tool_version="tv-000001-aaaaaaaa",
                    result={"status": "applied", "x": 1}, finished_at="t2")
    assert db.get_apply("ap-1")["result"] == {"status": "applied", "x": 1}
    assert db.find_done_apply("wf", "cs-1")["applyId"] == "ap-1"


def test_failed_apply_is_not_done(db):
    db.create_apply({"applyId": "ap-1", "targetId": "wf", "changeSetId": "cs-1", "status": "running", "startedAt": "t"})
    db.finish_apply("ap-1", status="failed", error_code="X", tool_version=None, result={"status": "failed"},
                    finished_at="t2")
    assert db.find_done_apply("wf", "cs-1") is None


def test_interrupt_running(db):
    db.create_apply({"applyId": "ap-1", "targetId": "wf", "changeSetId": None, "status": "running", "startedAt": "t"})
    assert db.interrupt_running("t2") == 1
    rec = db.get_apply("ap-1")
    assert rec["status"] == "failed" and rec["result"]["error"]["code"] == "INTERRUPTED"


def test_reviews(db):
    with db.transaction() as tx:
        tx.add_review({"reviewId": "rv-1", "targetId": "wf", "applyId": "ap-1", "kind": "tool", "method": "DELETE",
                       "path": "/pages/{id}", "operationId": "pages.delete", "toolName": "delete_page",
                       "reasons": ["DANGER_ADDED"], "definition": {"name": "delete_page"}, "previousRevision": None,
                       "verify": {"result": "passed"}, "status": "pending", "fragmentSha256": "a" * 64})
    assert [r["reviewId"] for r in db.reviews("wf", status="pending")] == ["rv-1"]
    db.decide_review("rv-1", decision="approved", by="me", note="ok", at="t")
    assert db.review("rv-1")["status"] == "approved"
    with db.transaction() as tx:
        tx.set_review_status("rv-1", "applied", applied_in="tv-000002-bbbbbbbb")
    assert db.review("rv-1")["appliedIn"] == "tv-000002-bbbbbbbb"


def test_supersede_pending_for_operation(db):
    with db.transaction() as tx:
        tx.add_review({"reviewId": "rv-1", "targetId": "wf", "applyId": "ap-1", "kind": "tool", "method": "GET",
                       "path": "/x", "operationId": "x", "toolName": "x_tool", "reasons": ["R"], "definition": None,
                       "previousRevision": None, "verify": None, "status": "pending", "fragmentSha256": None})
        tx.supersede_reviews("wf", "GET", "/x")
    assert db.review("rv-1")["status"] == "superseded"


def test_llm_cache(db):
    assert db.llm_cached("k") is None
    db.llm_store("k", task="tool_create", model="m", prompt="tool_create@v1", output={"a": 1}, usage={"t": 3}, at="t")
    assert db.llm_cached("k") == {"a": 1}


def test_index_state(db):
    db.index_upsert("wf", "add_comment", point_id="p1", revision="r-1", content_sha256="c", at="t")
    db.index_upsert("wf", "add_comment", point_id="p1", revision="r-2", content_sha256="d", at="t")
    assert db.index_state("wf") == {"add_comment": {"pointId": "p1", "revision": "r-2", "contentSha256": "d"}}
    db.index_delete("wf", "add_comment")
    assert db.index_state("wf") == {}


def test_unwritable_path_is_storage_error(tmp_path):
    (tmp_path / "f").write_text("x")
    with pytest.raises(StorageError):
        SqliteManufactureDb(tmp_path / "f" / "db")
