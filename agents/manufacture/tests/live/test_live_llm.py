"""LLM 실호출 (AC-8, AC-3b의 1~4). A4W_LIVE_TESTS=1 이고 LLM 비밀이 있을 때만 돈다.

seed에서 일부러 뺀 실제 동작 1개와, 모델이 미리 알 수 없는 가상 동작 2개를 실제 LLM으로 만든다.
출력 전문은 표준 출력에 남겨 test 결과서에 옮긴다(비밀 없음).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from a4w_clients.llm.openai_compatible import OpenAiCompatibleClient
from a4w_manufacture.assist.assistant import LlmBudget, ToolAssistant
from a4w_manufacture.assist.prompts import PromptSet, parse_versions
from a4w_manufacture.catalog import FileCatalog
from a4w_manufacture.checks import ID_LIKE, DefinitionChecker, TextLimits
from a4w_manufacture.selection import Selector
from a4w_manufacture.transform.openapi3 import OpenApi3Transformer

from ..specs import catalog_dir, fragment

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("A4W_LIVE_TESTS") != "1" or not os.environ.get("LLM_API_KEY"),
                       reason="A4W_LIVE_TESTS=1 이고 LLM 비밀이 있을 때만"),
]

PROMPTS = Path(__file__).resolve().parents[2] / "prompts"

WATCH = {  # 가상 동작: 명세에 없다. 모델이 미리 알 수 없다
    "operationId": "pages.watch", "method": "POST", "path": "/pages/{id}/watch", "tags": ["pages"],
    "summary": "페이지 지켜보기",
    "description": "페이지가 바뀌면 알림을 받도록 지켜본다. `scope`가 tree면 하위 페이지까지 지켜본다(기본 page). 이미 지켜보면 그대로 둔다.",
    "parameters": [{"in": "path", "name": "id", "required": True, "schema": {"type": "string"}, "description": "페이지의 id"}],
    "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "properties": {
        "scope": {"type": "string", "enum": ["page", "tree"], "default": "page", "description": "page(그 페이지만)·tree(하위까지)"}}}}}},
    "responses": {"200": {"content": {"application/json": {"schema": {"type": "object", "properties": {
        "watching": {"type": "boolean"}, "scope": {"type": "string"}}}}}}},
    "security": [{"bearerAuth": ["write"]}],
}
BACKLINKS = {
    "operationId": "pages.backlinks", "method": "GET", "path": "/pages/{id}/backlinks", "tags": ["pages"],
    "summary": "이 페이지를 가리키는 페이지",
    "description": "본문에서 이 페이지로 링크를 건 페이지 목록. 한 번에 `limit`(기본 50)개까지 오고, 더 있으면 `nextCursor`가 온다 — 다음 쪽은 `cursor`에 그 값을 넣어 다시 부른다.",
    "parameters": [
        {"in": "path", "name": "id", "required": True, "schema": {"type": "string"}, "description": "페이지의 id"},
        {"in": "query", "name": "cursor", "required": False, "schema": {"type": "string"}, "description": "앞 응답의 nextCursor"},
        {"in": "query", "name": "limit", "required": False, "schema": {"type": "integer", "default": 50, "maximum": 200}}],
    "responses": {"200": {"content": {"application/json": {"schema": {"type": "object", "properties": {
        "items": {"type": "array", "items": {"type": "object", "properties": {"pageId": {"type": "string"}, "title": {"type": "string"}}}},
        "nextCursor": {"type": ["string", "null"]}}}}}}},
    "security": [{"bearerAuth": ["read"]}],
}


@pytest.fixture(scope="module")
def assistant():
    env = os.environ
    http = httpx.Client(trust_env=True)
    llm = OpenAiCompatibleClient(base_url=env["LLM_BASE_URL"], api_key=env["LLM_API_KEY"],
                                 response_format=env["LLM_RESPONSE_FORMAT"], max_retries=int(env["LLM_MAX_RETRIES"]),
                                 http=http)

    class Cache:
        def llm_cached(self, key):
            return None

        def llm_store(self, key, **kw):
            pass

    checker = DefinitionChecker(TextLimits.parse(env["TOOL_TEXT_LIMITS"]),
                                max_visible_inputs=int(env["TOOL_MAX_VISIBLE_INPUTS"]))
    return ToolAssistant(
        llm=llm, cache=Cache(), prompts=PromptSet(PROMPTS, parse_versions(env["PROMPT_VERSIONS"])), checker=checker,
        model=env["LLM_MODEL"], timeout_seconds=int(env["LLM_TIMEOUT_SECONDS"]),
        max_output_tokens=int(env["LLM_MAX_OUTPUT_TOKENS"]), temperature=float(env["LLM_TEMPERATURE"]),
        reasoning_effort=env.get("LLM_REASONING_EFFORT") or None, max_retries=int(env["LLM_MAX_RETRIES"]),
        budget=LlmBudget(int(env["LLM_MAX_CALLS_PER_APPLY"])),
        clock=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


CATALOG = FileCatalog(catalog_dir())
SELECTOR = Selector(CATALOG.rules(), enabled_groups=["read", "history", "communication", "authoring"],
                    token_scopes={"read", "write"})
T = OpenApi3Transformer(content_types=["application/json"])


def create(assistant, frag, exclude_name=None):
    sk = T.skeleton(frag, fixed=SELECTOR.fixed_values(frag))
    sel = SELECTOR.select(frag)
    taken = {t["name"] for t in CATALOG.seeds()} - ({exclude_name} if exclude_name else set())
    out = assistant.author("create", sk, frag, previous=None, taken_names=taken,
                           examples=CATALOG.examples(group=sel.group, method=frag["method"], count=3, exclude=exclude_name))
    print(f"\n=== {frag['operationId']} (calls={out.calls}, usage={out.usage})")
    print(json.dumps(out.raw, ensure_ascii=False, indent=1))
    return out, taken


def common_asserts(out, taken):
    raw = out.raw
    assert raw["name"] not in taken                        # (2) 기존 이름과 겹치지 않음
    assert not any(ID_LIKE.search(e) for e in raw["index"]["examples"])  # (4) id 없는 자연스러운 요청
    assert len(raw["index"]["examples"]) >= 3


def test_real_operation_left_out_of_seed(assistant):
    """seed에서 뺀 실제 동작(comments.list)을 새로 만든다. 검사 통과는 assistant가 보장한다(통과 못 하면 예외)."""
    out, taken = create(assistant, fragment("comments.list"), exclude_name="list_comments")
    common_asserts(out, taken)
    assert set(out.raw["inputs"]) == {"pageId", "format"}   # (1) 칸을 빠짐없이, 지어내지 않음


def test_virtual_watch_operation(assistant):
    out, taken = create(assistant, WATCH)
    common_asserts(out, taken)
    text = out.raw["description"] + " ".join(v["description"] for v in out.raw["inputs"].values())
    assert "tree" in text or "하위" in text                  # (3) 명세의 범위 옵션이 설명에


def test_virtual_backlinks_operation(assistant):
    out, taken = create(assistant, BACKLINKS)
    common_asserts(out, taken)
    text = out.raw["description"] + " ".join(v["description"] for v in out.raw["inputs"].values())
    assert "cursor" in text.lower() or "nextCursor" in text or "다음" in text   # (3) 쪽 넘김 제약
