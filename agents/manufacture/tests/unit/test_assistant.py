"""LLM 보조 (Phase 2 설계 9절): 작업 셋, 이름 고정, 바뀌지 않은 칸 설명 옮기기, 재시도, 재사용, 상한."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from a4w_clients.llm.base import LlmClient, LlmError, LlmResult, LlmUsage
from a4w_manufacture.assembly import assemble, parse_authoring
from a4w_manufacture.assist.assistant import AssistFailed, LlmBudget, ToolAssistant
from a4w_manufacture.assist.prompts import PromptSet
from a4w_manufacture.checks import DefinitionChecker, TextLimits
from a4w_manufacture.transform.openapi3 import OpenApi3Transformer

from ..specs import fragment

PROMPTS = Path(__file__).resolve().parents[2] / "prompts"
T = OpenApi3Transformer(content_types=["application/json"])
SRC = {"operationId": "comments.create", "method": "POST", "path": "/pages/{pageId}/comments", "specVersion": "v",
       "fragmentSha256": "a" * 64, "structureSha256": "b" * 64}


class FakeLlm(LlmClient):
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return LlmResult(value=copy.deepcopy(out), model="m", usage=LlmUsage(1, 2, 0), attempts=1)


class MemoryCache:
    def __init__(self):
        self.items = {}

    def llm_cached(self, key):
        return self.items.get(key)

    def llm_store(self, key, *, task, model, prompt, output, usage, at):
        self.items[key] = output


def good(**over):
    out = {"name": "add_comment", "title": "댓글 달기", "description": "페이지에 댓글을 단다.",
           "inputs": {"pageId": {"description": "페이지 id"}, "format": {"description": "f", "hide": True, "default": "json"},
                      "body": {"description": "본문"}, "body_format": {"description": "f", "hide": True, "default": "markdown"},
                      "parentId": {"description": "상위 댓글"}},
           "responseFields": ["id"],
           "index": {"summary": "댓글을 남긴다", "examples": ["댓글 달아줘", "답글 남겨줘", "의견 적어줘"]}}
    out.update(over)
    return out


def assistant(llm, cache=None, budget=10):
    return ToolAssistant(
        llm=llm, cache=cache or MemoryCache(), prompts=PromptSet(PROMPTS, {"common": "v1", "tool_create": "v1",
                                                                              "tool_update": "v1", "tool_redescribe": "v1"}),
        checker=DefinitionChecker(TextLimits(600, 120, 60), max_visible_inputs=4), model="m", timeout_seconds=10, max_output_tokens=1000,
        temperature=0.2, reasoning_effort=None, max_retries=2, budget=LlmBudget(budget), clock=lambda: "t",
    )


def sk(frag=None):
    return T.skeleton(frag or fragment("comments.create"), fixed={})


def test_create_returns_checked_output_and_prompt_has_skeleton():
    llm = FakeLlm(good())
    out = assistant(llm).author("create", sk(), fragment("comments.create"), previous=None, taken_names=set(),
                                examples=[])
    assert out.raw["name"] == "add_comment" and out.origin == "llm" and out.calls == 1
    user = json.loads(llm.requests[0].user)
    assert [i["name"] for i in user["inputs"]] == ["pageId", "format", "body", "body_format", "parentId"]
    assert "items[]" not in json.dumps(user)  # 이 동작의 응답에 배열이 없다
    assert "이번 일은 새로 추가된" in llm.requests[0].system


def test_check_failure_is_retried_with_errors():
    bad = good()
    del bad["inputs"]["parentId"]
    llm = FakeLlm(bad, good())
    out = assistant(llm).author("create", sk(), fragment("comments.create"), previous=None, taken_names=set(),
                                examples=[])
    assert out.calls == 2
    assert "INPUT_MISSING" in llm.requests[1].user


def test_gives_up_after_retries():
    bad = good(name="Bad Name")
    with pytest.raises(AssistFailed) as e:
        assistant(FakeLlm(bad, bad, bad)).author("create", sk(), fragment("comments.create"), previous=None,
                                                  taken_names=set(), examples=[])
    assert e.value.code == "LLM_OUTPUT_INVALID"
    assert e.value.problems[0]["code"] == "NAME_FORMAT"


def test_cache_reuses_output_without_calling():
    cache = MemoryCache()
    assistant(FakeLlm(good()), cache).author("create", sk(), fragment("comments.create"), previous=None,
                                              taken_names=set(), examples=[])
    llm = FakeLlm()
    out = assistant(llm, cache).author("create", sk(), fragment("comments.create"), previous=None, taken_names=set(),
                                        examples=[])
    assert out.origin == "cache" and llm.requests == []


def previous_definition():
    return assemble(sk(), parse_authoring(good(), max_chars=2000), group="communication", risk="write", origin="seed",
                    source=SRC)


def test_update_forces_previous_name_and_keeps_unchanged_descriptions():
    frag = copy.deepcopy(fragment("comments.create"))
    # 본문에 새 칸 mention 추가
    schema = frag["requestBody"]["content"]["application/json"]["schema"]
    schema["properties"]["mention"] = {"type": "string", "description": "멘션할 사용자"}
    llm_out = good(name="create_comment")
    llm_out["inputs"]["pageId"]["description"] = "LLM이 고쳐 쓴 설명"
    llm_out["inputs"]["mention"] = {"description": "멘션할 사용자 이름"}
    out = assistant(FakeLlm(llm_out)).author("update", sk(frag), frag, previous=previous_definition(),
                                             taken_names=set(), examples=[])
    assert out.raw["name"] == "add_comment"
    assert out.raw["inputs"]["pageId"]["description"] == "페이지 id"     # 바뀌지 않은 칸은 이전 설명
    assert out.raw["inputs"]["mention"]["description"] == "멘션할 사용자 이름"  # 새 칸은 LLM


def test_redescribe_keeps_non_text_fields():
    llm_out = good(name="other", responseFields=["createdAt"])
    llm_out["inputs"]["format"] = {"description": "새 설명"}  # 숨김을 풀려고 해도
    out = assistant(FakeLlm(llm_out)).author("redescribe", sk(), fragment("comments.create"),
                                             previous=previous_definition(), taken_names=set(), examples=[])
    assert out.raw["name"] == "add_comment"
    assert out.raw["responseFields"] == ["id"]
    assert out.raw["inputs"]["format"]["hide"] is True and out.raw["inputs"]["format"]["default"] == "json"
    assert out.raw["inputs"]["format"]["description"] == "새 설명"
    assert out.origin == "llm-doc"


def test_budget_exceeded():
    with pytest.raises(AssistFailed) as e:
        assistant(FakeLlm(good()), budget=0).author("create", sk(), fragment("comments.create"), previous=None,
                                                    taken_names=set(), examples=[])
    assert e.value.code == "LLM_BUDGET_EXCEEDED"


@pytest.mark.parametrize("kind,code", [("timeout", "LLM_TIMEOUT"), ("unavailable", "LLM_UNAVAILABLE"),
                                       ("output_format", "LLM_OUTPUT_INVALID"), ("rate_limit", "LLM_UNAVAILABLE")])
def test_llm_errors_map_to_codes(kind, code):
    with pytest.raises(AssistFailed) as e:
        assistant(FakeLlm(LlmError(kind, "x"))).author("create", sk(), fragment("comments.create"), previous=None,
                                                       taken_names=set(), examples=[])
    assert e.value.code == code


def test_auth_error_propagates_as_fatal():
    from a4w_manufacture.errors import LlmAuthFailed

    with pytest.raises(LlmAuthFailed):
        assistant(FakeLlm(LlmError("auth", "x"))).author("create", sk(), fragment("comments.create"), previous=None,
                                                         taken_names=set(), examples=[])


def test_prompt_set_reads_versioned_files_and_unknown_version_fails():
    ps = PromptSet(PROMPTS, {"common": "v1", "tool_create": "v1"})
    text, pid = ps.get("tool_create")
    assert "{{common}}" not in text and "snake_case" in text
    assert pid == "tool_create@v1+common@v1"
    from a4w_core.errors import ConfigError

    with pytest.raises(ConfigError):
        PromptSet(PROMPTS, {"common": "v9", "tool_create": "v1"}).get("tool_create")
