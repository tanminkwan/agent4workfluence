"""시나리오 값 채우기와 자동 시나리오 (Phase 2 설계 11.7절)."""

from __future__ import annotations

import pytest

from a4w_detection.verify.auto import auto_scenario
from a4w_detection.verify.scenarios import ScenarioError, fill

from ..verify_fakes import CONTEXT, add_comment, read_page, search_pages

VARS = {"sandbox": CONTEXT, "run": {"id": "vf-1"}, "saved": {"cid": "c9"}, "setup": {"0": {"id": "p1"}}}


def test_whole_placeholder_keeps_type():
    assert fill("${sandbox.currentVersionNo}", VARS) == 1


def test_embedded_placeholder_becomes_text():
    assert fill("댓글 ${run.id} 끝", VARS) == "댓글 vf-1 끝"


def test_nested_structures():
    assert fill({"a": ["${saved.cid}", {"b": "${setup.0.id}"}], "n": 3}, VARS) == {"a": ["c9", {"b": "p1"}], "n": 3}


def test_unknown_placeholder_fails():
    with pytest.raises(ScenarioError):
        fill("${sandbox.nope}", VARS)


def test_auto_fills_path_from_context_and_strings():
    sc = auto_scenario(read_page(), CONTEXT)
    assert sc["call"]["args"] == {"pageId": "p1"}
    assert sc["expect"] == {"ok": True}
    assert auto_scenario(search_pages(), CONTEXT)["call"]["args"] == {"q": "a4w"}


def test_auto_cannot_fill_unknown_path_input():
    t = read_page()
    t["inputSchema"]["properties"] = {"docId": {"type": "string"}}
    t["inputSchema"]["required"] = ["docId"]
    t["http"]["bindings"] = [{"input": "docId", "in": "path", "name": "id"}]
    assert auto_scenario(t, CONTEXT) is None


def test_auto_uses_enum_and_default():
    t = search_pages()
    t["inputSchema"]["properties"]["mode"] = {"type": "string", "enum": ["fast", "slow"]}
    t["inputSchema"]["properties"]["size"] = {"type": "integer", "default": 5}
    t["inputSchema"]["required"] = ["q", "mode", "size"]
    assert auto_scenario(t, CONTEXT)["call"]["args"] == {"q": "a4w", "mode": "fast", "size": 5}


def test_auto_cannot_fill_required_integer_without_default():
    t = search_pages()
    t["inputSchema"]["properties"]["size"] = {"type": "integer"}
    t["inputSchema"]["required"] = ["q", "size"]
    assert auto_scenario(t, CONTEXT) is None


def test_no_auto_for_write_tools():
    assert auto_scenario(add_comment(), CONTEXT) is None
