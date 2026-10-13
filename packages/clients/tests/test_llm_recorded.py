"""LlmClient 계약 시험 — 실제 엔드포인트에서 녹화한 응답으로 (Phase 2 설계 10.2절, AC-7).

녹화는 live/test_live_llm_record.py가 한다. 녹화본이 없으면 건너뛴다.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from a4w_clients.llm.base import LlmError, LlmRequest
from a4w_clients.llm.openai_compatible import OpenAiCompatibleClient

REC = Path(__file__).parent / "contract" / "recordings"
SCHEMA = {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}},
          "additionalProperties": False}


def replay(case: str, times: int = 1):
    path = REC / f"{case}.json"
    if not path.is_file():
        pytest.skip(f"녹화본 없음: {path.name}")
    rec = json.loads(path.read_text(encoding="utf-8"))
    responses = rec["responses"] * times

    def handler(req: httpx.Request) -> httpx.Response:
        r = responses.pop(0)
        body = r["body"]
        content = json.dumps(body).encode() if "_text" not in body else body["_text"].encode()
        return httpx.Response(r["status"], headers=r["headers"], content=content)

    return OpenAiCompatibleClient(base_url="https://llm.test", api_key="k", response_format="json_object",
                                  max_retries=times - 1, http=httpx.Client(transport=httpx.MockTransport(handler)),
                                  sleep=lambda s: None), rec


def request():
    return LlmRequest(system="s", user="u", output_schema=SCHEMA, model="m", timeout_seconds=30, max_output_tokens=100)


def test_recorded_normal():
    c, rec = replay("normal")
    r = c.complete(request())
    assert r.value == json.loads(rec["responses"][0]["body"]["choices"][0]["message"]["content"])
    assert r.model == rec["model"]
    assert r.usage.reasoning_tokens == rec["responses"][0]["body"]["usage"]["completion_tokens_details"]["reasoning_tokens"]


def test_recorded_length_is_output_format_after_retries():
    c, _ = replay("length", times=3)
    with pytest.raises(LlmError) as e:
        c.complete(request())
    assert e.value.kind == "output_format" and e.value.attempts == 3


def test_recorded_401_is_auth_without_retry():
    c, _ = replay("auth", times=1)
    with pytest.raises(LlmError) as e:
        c.complete(request())
    assert e.value.kind == "auth"
