"""색인 맞추기 (Phase 2 설계 14.3·14.4절)."""

from __future__ import annotations

import json
import uuid

from a4w_clients.rag.base import RagError, RagSpace, RagSpaceRef, RagStore
from a4w_manufacture.index.sync import IndexSync, index_document, point_id
from a4w_manufacture.storage.sqlite_db import SqliteManufactureDb

from ..tool_fakes import tool

SPACE = RagSpace(collection="a4w_tools_wf", domain="a4w-tools-wf", description="d")


class FakeRag(RagStore):
    def __init__(self):
        self.points: dict[str, dict] = {}
        self.fail_ids: set[str] = set()
        self.fail_space = False

    def ensure_space(self, space):
        if self.fail_space:
            raise RagError("down")
        return RagSpaceRef(space.collection, 1)

    def upsert(self, ref, doc):
        if doc.id in self.fail_ids:
            raise RagError("x")
        self.points[doc.id] = {"content": doc.content, "source": doc.source, "ext": doc.extended_content}

    def delete(self, ref, doc_id):
        self.points.pop(doc_id, None)

    def list_ids(self, ref, *, limit):
        return {k: v["source"] for k, v in self.points.items()}

    def search(self, ref, query, *, limit):
        return []


def make(tmp_path, rag=None):
    db = SqliteManufactureDb(tmp_path / "m.db")
    return IndexSync(rag or FakeRag(), db, content_max_chars=200, list_limit=100, clock=lambda: "t"), db


def tools(*names):
    return {n: tool(n) for n in names}


def test_point_id_is_deterministic_uuid():
    assert point_id("wf", "add_comment") == point_id("wf", "add_comment")
    assert point_id("wf", "add_comment") != point_id("other", "add_comment")
    uuid.UUID(point_id("wf", "add_comment"))


def test_document_shape():
    t = tool("add_comment")
    doc = index_document("wf", t, tool_version="tv-000001-aaaaaaaa", max_chars=200)
    assert doc.source == f"wf/add_comment@{t['revision']}"
    assert doc.content.startswith("add_comment. add_comment")  # title. summary
    assert "가 / 나 / 다" in doc.content
    ext = json.loads(doc.extended_content)
    assert ext["name"] == "add_comment" and ext["revision"] == t["revision"] and ext["toolVersion"] == "tv-000001-aaaaaaaa"
    long = dict(t, title="가" * 500)
    assert len(index_document("wf", long, tool_version="v", max_chars=200).content) <= 200


def test_first_sync_upserts_all(tmp_path):
    sync, db = make(tmp_path)
    out = sync.sync("wf", SPACE, tools("a_tool", "b_tool"), tool_version="v1")
    assert out == {"upserted": 2, "deleted": 0, "failed": 0, "drift": False}
    assert set(db.index_state("wf")) == {"a_tool", "b_tool"}


def test_only_changes_are_sent(tmp_path):
    rag = FakeRag()
    sync, _ = make(tmp_path, rag)
    ts = tools("a_tool", "b_tool")
    sync.sync("wf", SPACE, ts, tool_version="v1")
    changed = dict(ts)
    changed["b_tool"] = dict(tool("b_tool"), title="바뀐 제목", revision="r-bbbbbbbbbbbb")
    del changed["a_tool"]
    out = sync.sync("wf", SPACE, changed, tool_version="v2")
    assert out == {"upserted": 1, "deleted": 1, "failed": 0, "drift": False}
    assert list(rag.points) == [point_id("wf", "b_tool")]


def test_failure_records_drift_and_next_sync_fixes(tmp_path):
    rag = FakeRag()
    sync, db = make(tmp_path, rag)
    rag.fail_ids = {point_id("wf", "b_tool")}
    out = sync.sync("wf", SPACE, tools("a_tool", "b_tool"), tool_version="v1")
    assert out["failed"] == 1 and out["drift"] is True
    assert set(db.index_state("wf")) == {"a_tool"}
    rag.fail_ids = set()
    out = sync.sync("wf", SPACE, tools("a_tool", "b_tool"), tool_version="v1")
    assert out == {"upserted": 1, "deleted": 0, "failed": 0, "drift": False}


def test_orphans_with_our_source_prefix_are_removed(tmp_path):
    rag = FakeRag()
    rag.points["orphan"] = {"content": "", "source": "wf/gone_tool@r-1", "ext": ""}
    rag.points["foreign"] = {"content": "", "source": "someone/else", "ext": ""}
    sync, _ = make(tmp_path, rag)
    sync.sync("wf", SPACE, tools("a_tool"), tool_version="v1")
    assert "orphan" not in rag.points and "foreign" in rag.points


def test_space_failure_is_drift_not_exception(tmp_path):
    rag = FakeRag()
    rag.fail_space = True
    sync, _ = make(tmp_path, rag)
    out = sync.sync("wf", SPACE, tools("a_tool"), tool_version="v1")
    assert out["drift"] is True and out["failed"] == 1
