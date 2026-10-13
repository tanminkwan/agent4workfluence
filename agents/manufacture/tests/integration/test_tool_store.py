"""tool-store 묶음 쓰기·포인터 교체·되돌리기 (Phase 2 설계 13.1·13.2절)."""

from __future__ import annotations

import json

import pytest

from a4w_manufacture.storage.bundles import FileToolStore
from a4w_tools.reader import ToolsetReader

from ..tool_fakes import tool


def meta(**over):
    m = {"specVersion": "s" * 64, "changeSetId": "cs-1", "createdAt": "2026-10-13T00:00:00Z", "verified": {}}
    m.update(over)
    return m


def test_write_then_point_and_read(tmp_path):
    store = FileToolStore(tmp_path)
    tools = {"add_comment": tool("add_comment"), "read_page": tool("read_page")}
    v1 = store.write("wf", apply_id="ap-1", tools=tools, parent=None, meta=meta())
    assert v1.startswith("tv-000001-")
    assert store.current("wf") is None  # 포인터는 아직
    store.set_current("wf", v1)
    ts = ToolsetReader(tmp_path).current("wf")  # Phase 3이 읽는 방식 그대로
    assert set(ts.tools) == {"add_comment", "read_page"}
    assert ts.manifest["parent"] is None
    assert not (tmp_path / "targets/wf/staging/ap-1").exists()


def test_versions_are_sequential_and_immutable(tmp_path):
    store = FileToolStore(tmp_path)
    v1 = store.write("wf", apply_id="ap-1", tools={"a_tool": tool("a_tool")}, parent=None, meta=meta())
    v2 = store.write("wf", apply_id="ap-2", tools={"b_tool": tool("b_tool")}, parent=v1, meta=meta())
    assert v2.startswith("tv-000002-")
    assert store.versions("wf") == [v1, v2]
    assert store.read("wf", v2).manifest["parent"] == v1
    with pytest.raises(FileExistsError):
        store._finalize("wf", "ap-x", v1)  # 같은 버전 디렉터리를 덮어쓰지 않는다


def test_manifest_records_verified_and_operation(tmp_path):
    store = FileToolStore(tmp_path)
    t = tool("add_comment")
    v = store.write("wf", apply_id="ap-1", tools={"add_comment": t}, parent=None,
                    meta=meta(verified={"add_comment": "unverified"}))
    entry = store.read("wf", v).manifest["tools"][0]
    assert entry["verified"] == "unverified"
    assert entry["operationId"] == t["source"]["operationId"]
    assert entry["revision"] == t["revision"]


def test_set_current_is_atomic_file_replace(tmp_path):
    store = FileToolStore(tmp_path)
    v1 = store.write("wf", apply_id="ap-1", tools={"a_tool": tool("a_tool")}, parent=None, meta=meta())
    store.set_current("wf", v1)
    pointer = tmp_path / "targets/wf/current.json"
    assert json.loads(pointer.read_text())["toolVersion"] == v1
    assert not list(pointer.parent.glob("*.tmp"))


def test_set_current_to_unknown_version_fails(tmp_path):
    with pytest.raises(FileNotFoundError):
        FileToolStore(tmp_path).set_current("wf", "tv-000009-aaaaaaaa")


def test_clean_staging(tmp_path):
    store = FileToolStore(tmp_path)
    (tmp_path / "targets/wf/staging/ap-dead").mkdir(parents=True)
    assert store.clean_staging("wf") == 1
    assert not (tmp_path / "targets/wf/staging/ap-dead").exists()
