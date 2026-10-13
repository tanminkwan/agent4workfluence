"""ToolsetReader: 현재 포인터 → 묶음 읽기."""

from __future__ import annotations

import json

import pytest

from a4w_tools.reader import ToolsetNotFound, ToolsetReader


def write_bundle(root, target, version, tools):
    vdir = root / "targets" / target / "versions" / version
    (vdir / "tools").mkdir(parents=True)
    entries = []
    for t in tools:
        (vdir / "tools" / f"{t['name']}.json").write_text(json.dumps(t), encoding="utf-8")
        entries.append(
            {
                "name": t["name"],
                "kind": t["kind"],
                "file": f"tools/{t['name']}.json",
                "revision": t["revision"],
                "risk": t["risk"],
                "scopes": t["scopes"],
                "operationId": t.get("source", {}).get("operationId"),
                "verified": "passed",
            }
        )
    manifest = {
        "schema": "a4w.toolset/v1",
        "targetId": target,
        "toolVersion": version,
        "parent": None,
        "specVersion": "s" * 64,
        "changeSetId": "cs-1",
        "createdAt": "2026-10-13T00:00:00Z",
        "tools": entries,
    }
    (vdir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def point(root, target, version):
    (root / "targets" / target / "current.json").write_text(
        json.dumps({"toolVersion": version, "updatedAt": "t"}), encoding="utf-8"
    )


def test_reads_current_version(tmp_path, op_tool, search_tool):
    write_bundle(tmp_path, "wf", "tv-000001-aaaaaaaa", [op_tool, search_tool])
    point(tmp_path, "wf", "tv-000001-aaaaaaaa")
    ts = ToolsetReader(tmp_path).current("wf")
    assert ts.tool_version == "tv-000001-aaaaaaaa"
    assert set(ts.tools) == {"add_comment", "search_pages"}
    assert ts.manifest["targetId"] == "wf"


def test_no_pointer_is_not_found(tmp_path):
    with pytest.raises(ToolsetNotFound):
        ToolsetReader(tmp_path).current("wf")


def test_pointer_to_missing_version_is_not_found(tmp_path):
    (tmp_path / "targets" / "wf").mkdir(parents=True)
    point(tmp_path, "wf", "tv-000009-bbbbbbbb")
    with pytest.raises(ToolsetNotFound):
        ToolsetReader(tmp_path).current("wf")


def test_revision_mismatch_between_manifest_and_file_is_rejected(tmp_path, op_tool):
    write_bundle(tmp_path, "wf", "tv-000001-aaaaaaaa", [op_tool])
    point(tmp_path, "wf", "tv-000001-aaaaaaaa")
    f = tmp_path / "targets/wf/versions/tv-000001-aaaaaaaa/tools/add_comment.json"
    t = json.loads(f.read_text())
    t["revision"] = "r-ffffffffffff"
    f.write_text(json.dumps(t))
    with pytest.raises(ToolsetNotFound):
        ToolsetReader(tmp_path).current("wf")
