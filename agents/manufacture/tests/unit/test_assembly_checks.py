"""정의 조립(골격 + 글)과 검사 (Phase 2 설계 6.1·8·9.2절)."""

from __future__ import annotations

import copy

import pytest

from a4w_manufacture.assembly import assemble, extract_authoring, parse_authoring, revision_of
from a4w_manufacture.checks import DefinitionChecker, TextLimits
from a4w_manufacture.transform.openapi3 import OpenApi3Transformer
from a4w_tools.validate import validate_tool

from ..specs import fragment

T = OpenApi3Transformer(content_types=["application/json"])
LIMITS = TextLimits(description=600, summary=120, example=60)
MAX_VISIBLE = 4
SOURCE = {"operationId": "comments.create", "method": "POST", "path": "/pages/{pageId}/comments",
          "specVersion": "v", "fragmentSha256": "a" * 64, "structureSha256": "b" * 64}


def skeleton():
    return T.skeleton(fragment("comments.create"), fixed={})


def llm_output(**over):
    out = {
        "name": "add_comment",
        "title": "페이지에 댓글 달기",
        "description": "페이지에 댓글을 단다. 본문은 마크다운이다.",
        "inputs": {
            "pageId": {"description": "페이지 id"},
            "format": {"description": "응답 형식", "hide": True, "default": "json"},
            "body": {"description": "댓글 본문"},
            "body_format": {"description": "본문 형식", "hide": True, "default": "markdown"},
            "parentId": {"description": "답글이면 상위 댓글 id"},
        },
        "responseFields": ["id", "createdAt"],
        "index": {"summary": "페이지에 댓글을 남긴다", "examples": ["회의록에 댓글 달아줘", "답글 남겨줘", "의견 적어줘"]},
    }
    out.update(over)
    return out


def build(raw=None):
    a = parse_authoring(raw or llm_output(), max_chars=2000)
    return assemble(skeleton(), a, group="communication", risk="write", origin="llm", source=SOURCE)


def codes(problems):
    return {p.code for p in problems}


def test_assembled_definition_is_valid_and_hides_inputs():
    d = build()
    assert validate_tool(d) == []
    props = d["inputSchema"]["properties"]
    # parentId는 명세 기본값이 있는 선택 칸이라 기계 기본 숨김 → 보내지 않음 (설계 6.3.1)
    assert set(props) == {"pageId", "body"}
    assert d["inputSchema"]["required"] == ["pageId", "body"]
    assert d["http"]["omitted"] == ["parentId"]
    assert {"in": "query", "name": "format", "value": "json"} in d["http"]["fixed"]
    assert {"in": "body", "pointer": "/format", "value": "markdown"} in d["http"]["fixed"]
    assert [b["input"] for b in d["http"]["bindings"]] == ["pageId", "body"]
    assert props["body"]["description"] == "댓글 본문"
    assert d["response"] == {"fields": ["id", "createdAt"], "maxChars": 2000}
    assert d["revision"] == revision_of(d)


def test_revision_is_stable_and_content_based():
    assert build()["revision"] == build()["revision"]
    assert build()["revision"] != build(llm_output(title="다른 제목"))["revision"]


def test_extract_then_assemble_roundtrip():
    d = build()
    again = assemble(skeleton(), extract_authoring(d, skeleton()), group="communication", risk="write",
                     origin="llm", source=SOURCE)
    assert again == d


def check(raw, taken=()):
    return DefinitionChecker(LIMITS, max_visible_inputs=MAX_VISIBLE).check_authoring(skeleton(), raw, taken_names=set(taken))


def test_good_output_has_no_problems():
    assert check(llm_output()) == []


def test_input_missing_and_invented():
    raw = llm_output()
    del raw["inputs"]["parentId"]
    raw["inputs"]["madeUp"] = {"description": "x"}
    assert codes(check(raw)) == {"INPUT_MISSING", "INPUT_INVENTED"}


def test_hidden_required_without_default():
    raw = llm_output()
    raw["inputs"]["pageId"] = {"description": "x", "hide": True}
    assert "HIDDEN_REQUIRED" in codes(check(raw))


def test_default_must_match_schema():
    raw = llm_output()
    raw["inputs"]["format"] = {"description": "x", "hide": True, "default": "yaml"}  # enum 밖
    assert "DEFAULT_INVALID" in codes(check(raw))


@pytest.mark.parametrize("name", ["AddComment", "add-comment", "ab", "1st_tool"])
def test_name_format(name):
    assert "NAME_FORMAT" in codes(check(llm_output(name=name)))


def test_name_taken():
    assert "NAME_TAKEN" in codes(check(llm_output(), taken={"add_comment"}))


def test_response_field_unknown():
    assert "RESPONSE_FIELD_UNKNOWN" in codes(check(llm_output(responseFields=["id", "nope"])))


@pytest.mark.parametrize("example", ["3fa85f64-5717-4562-b3fc-2c963f66afa6 페이지에 댓글", "id 9f8e7d6c5b4a3f2e 페이지",
                                     "페이지 1234567에 댓글"])
def test_example_with_id(example):
    raw = llm_output()
    raw["index"]["examples"][0] = example
    assert "EXAMPLE_HAS_ID" in codes(check(raw))


def test_text_limits():
    raw = llm_output(description="가" * 601)
    raw["index"]["examples"] = ["하나", "둘"]
    assert codes(check(raw)) == {"TEXT_LIMIT"}


def test_output_schema_violation():
    raw = llm_output()
    del raw["index"]
    assert codes(check(raw)) == {"SCHEMA_INVALID"}


def test_seed_with_same_mapping_passes():
    seed = build()
    assert DefinitionChecker(LIMITS, max_visible_inputs=MAX_VISIBLE).check_seed(skeleton(), seed, taken_names=set()) == []


def test_seed_with_changed_mapping_is_rejected():
    seed = copy.deepcopy(build())
    seed["http"]["path"] = "/pages/{pageId}/notes"
    assert "MAPPING_CHANGED" in codes(DefinitionChecker(LIMITS, max_visible_inputs=MAX_VISIBLE).check_seed(skeleton(), seed, taken_names=set()))


def test_seed_missing_input_is_rejected():
    seed = copy.deepcopy(build())
    seed["http"]["omitted"] = []
    assert "INPUT_MISSING" in codes(DefinitionChecker(LIMITS, max_visible_inputs=MAX_VISIBLE).check_seed(skeleton(), seed, taken_names=set()))


def test_explicit_unhide_keeps_input_visible():
    raw = llm_output()
    raw["inputs"]["parentId"] = {"description": "답글이면 상위 댓글 id", "hide": False}
    d = build(raw)
    assert "parentId" in d["inputSchema"]["properties"] and "omitted" not in d["http"]


def test_hiding_optional_without_default_means_not_sent():
    raw = llm_output()
    raw["inputs"]["parentId"] = {"description": "x", "hide": True}
    assert check(raw) == []
    assert build(raw)["http"]["omitted"] == ["parentId"]


def test_too_many_visible_inputs():
    raw = llm_output()
    for name in ("format", "body_format", "parentId"):
        raw["inputs"][name] = {"description": "x", "hide": False}
    assert "TOO_MANY_INPUTS" in codes(check(raw))  # 보이는 칸 5 > 4
    c = DefinitionChecker(LIMITS, max_visible_inputs=2).check_authoring(skeleton(), llm_output(), taken_names=set())
    assert "TOO_MANY_INPUTS" not in codes(c)  # 보이는 칸은 pageId, body 둘
    c3 = DefinitionChecker(LIMITS, max_visible_inputs=1).check_authoring(skeleton(), llm_output(), taken_names=set())
    assert "TOO_MANY_INPUTS" in codes(c3)


def test_seed_round_trip_keeps_omitted():
    d = build()
    again = assemble(skeleton(), extract_authoring(d, skeleton()), group="communication", risk="write", origin="llm",
                     source=SOURCE)
    assert again == d
