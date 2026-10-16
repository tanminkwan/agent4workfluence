"""정의 + 입력 → HTTP 요청."""

from __future__ import annotations

import pytest

from a4w_tools.errors import ToolFailure
from a4w_tools.request import build_request


def test_path_query_body_and_fixed(op_tool):
    req = build_request(op_tool, {"pageId": "p 1/2", "body": "안녕"})
    assert req.method == "POST"
    assert req.path == "/pages/p%201%2F2/comments"
    assert req.json_body == {"body": "안녕", "format": "markdown"}
    assert req.query == {}
    assert req.is_write


def test_optional_input_left_out(op_tool):
    req = build_request(op_tool, {"pageId": "p1", "body": "x"})
    assert "parentId" not in req.json_body


def test_explicit_null_in_body_is_sent(op_tool):
    req = build_request(op_tool, {"pageId": "p1", "body": "x", "parentId": None})
    assert req.json_body["parentId"] is None


def test_query_mapping_renames_and_fixes(search_tool):
    req = build_request(search_tool, {"q": "회의록", "spaceKey": "ENG", "limit": 5})
    assert req.method == "GET"
    assert not req.is_write
    assert req.query == {"q": "회의록", "space": "ENG", "limit": "5", "format": "json"}
    assert req.json_body is None


def test_boolean_query_value(search_tool):
    search_tool["http"]["bindings"].append({"input": "flag", "in": "query", "name": "flag"})
    req = build_request(search_tool, {"q": "a", "flag": True})
    assert req.query["flag"] == "true"


def test_header_binding(search_tool):
    search_tool["http"]["bindings"].append({"input": "lang", "in": "header", "name": "Accept-Language"})
    req = build_request(search_tool, {"q": "a", "lang": "ko"})
    assert req.headers == {"Accept-Language": "ko"}


def test_nested_body_pointer(op_tool):
    op_tool["http"]["bindings"][1]["pointer"] = "/content/text"
    req = build_request(op_tool, {"pageId": "p", "body": "x"})
    assert req.json_body["content"] == {"text": "x"}


def test_whole_body_pointer(op_tool):
    op_tool["http"]["bindings"] = [
        {"input": "pageId", "in": "path", "name": "pageId"},
        {"input": "body", "in": "body", "pointer": ""},
    ]
    op_tool["http"]["fixed"] = []
    req = build_request(op_tool, {"pageId": "p", "body": ["a", "b"]})
    assert req.json_body == ["a", "b"]


def test_unfilled_path_param_is_input_error(op_tool):
    op_tool["http"]["path"] = "/pages/{pageId}/comments/{other}"
    with pytest.raises(ToolFailure) as e:
        build_request(op_tool, {"pageId": "p", "body": "x"})
    assert e.value.error.code == "INPUT_INVALID"


def test_multipart_is_unsupported(op_tool):
    op_tool["http"]["contentType"] = "multipart/form-data"
    with pytest.raises(ToolFailure) as e:
        build_request(op_tool, {"pageId": "p", "body": "x"})
    assert e.value.error.code == "UNSUPPORTED"


def test_context_fixed_value_is_filled(search_tool):
    search_tool["http"]["fixed"].append({"in": "query", "name": "space", "context": "homeSpace"})
    req = build_request(search_tool, {"q": "a"}, context={"homeSpace": "s-1"})
    assert req.query["space"] == "s-1"


def test_missing_context_value(search_tool):
    search_tool["http"]["fixed"].append({"in": "query", "name": "space", "context": "homeSpace"})
    with pytest.raises(ToolFailure) as e:
        build_request(search_tool, {"q": "a"}, context={})
    assert e.value.error.code == "CONTEXT_MISSING"


# ── 맥락 기본값 (Phase 2 설계 26절): 입력이 있으면 그 값, 없으면 맥락 값

def overridable(tool):
    tool["http"]["bindings"].append({"input": "space", "in": "query", "name": "space", "contextDefault": "homeSpace"})
    return tool


def test_context_default_is_used_when_the_input_is_left_out(search_tool):
    req = build_request(overridable(search_tool), {"q": "a"}, context={"homeSpace": "s-1"})
    assert req.query["space"] == "s-1"


def test_an_input_value_wins_over_the_context_default(search_tool):
    req = build_request(overridable(search_tool), {"q": "a", "space": "Crazy Study"}, context={"homeSpace": "s-1"})
    assert req.query["space"] == "Crazy Study"


def test_context_default_into_a_body_pointer(op_tool):
    op_tool["http"]["bindings"].append({"input": "space", "in": "body", "pointer": "/space", "contextDefault": "homeSpace"})
    req = build_request(op_tool, {"pageId": "p-1", "body": "안녕"}, context={"homeSpace": "s-1"})
    assert req.json_body["space"] == "s-1"


def test_missing_context_default_value_is_an_error_only_when_the_input_is_left_out(search_tool):
    tool = overridable(search_tool)
    assert build_request(tool, {"q": "a", "space": "x"}, context={}).query["space"] == "x"
    with pytest.raises(ToolFailure) as e:
        build_request(tool, {"q": "a"}, context={})
    assert e.value.error.code == "CONTEXT_MISSING"
