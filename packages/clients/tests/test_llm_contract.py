"""LlmClient 계약 시험 (Phase 2 설계 10.2절, AC-7). provider마다 같은 경우에 같은 결과 모양·오류 분류가 나와야 한다.

응답은 OpenAI 호환 /v1/chat/completions 모양이다. 실제 엔드포인트 응답 녹화는 live 시험이 남긴다.
"""

from __future__ import annotations

import json

import httpx
import pytest

from a4w_clients.llm.base import LlmError, LlmRequest
from a4w_clients.llm.openai_compatible import OpenAiCompatibleClient

SCHEMA = {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}},
          "additionalProperties": False}
KEY = "sk-secret-key"


def completion(content, finish="stop", reasoning="생각한 내용"):
    return {"id": "x", "model": "zai-org/GLM-5.2", "choices": [{"index": 0, "finish_reason": finish,
            "message": {"role": "assistant", "content": content, "reasoning_content": reasoning}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "completion_tokens_details": {"reasoning_tokens": 15}}}


class Server:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        assert request.url.path == "/v1/chat/completions"
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Sleeper:
    def __init__(self):
        self.slept = []

    def __call__(self, s):
        self.slept.append(s)


def client(server, mode="json_object", retries=2, sleeper=None):
    http = httpx.Client(transport=httpx.MockTransport(server))
    return OpenAiCompatibleClient(base_url="https://llm.test", api_key=KEY, response_format=mode, max_retries=retries,
                                  http=http, sleep=sleeper or Sleeper())


def request(**over):
    kw = dict(system="너는 tool 설계자다", user="{}", output_schema=SCHEMA, model="zai-org/GLM-5.2", timeout_seconds=30,
              max_output_tokens=1000, temperature=0.2, reasoning_effort=None)
    kw.update(over)
    return LlmRequest(**kw)


def ok(content, **kw):
    return httpx.Response(200, json=completion(content, **kw))


def test_normal():
    s = Server(ok('{"name": "add_comment"}'))
    r = client(s).complete(request())
    assert r.value == {"name": "add_comment"}
    assert r.model == "zai-org/GLM-5.2"
    assert r.attempts == 1
    assert r.usage.prompt_tokens == 10 and r.usage.reasoning_tokens == 15
    body = s.requests[0]
    assert body["response_format"] == {"type": "json_object"}
    assert "출력 JSON Schema" in body["messages"][0]["content"]
    assert "reasoning_effort" not in body
    assert body["max_tokens"] == 1000 and body["temperature"] == 0.2


def test_reasoning_content_is_not_returned():
    r = client(Server(ok('{"name": "x"}'))).complete(request())
    assert "생각한" not in repr(r)


def test_schema_mismatch_then_ok_feeds_back_error():
    s = Server(ok('{"title": "x"}'), ok('{"name": "x"}'))
    r = client(s).complete(request())
    assert r.attempts == 2
    assert "앞선 출력의 오류" in s.requests[1]["messages"][-1]["content"]


def test_not_json_then_ok():
    r = client(Server(ok("not json"), ok('{"name": "x"}'))).complete(request())
    assert r.attempts == 2


def test_length_keeps_failing_is_output_format():
    s = Server(*[ok('{"na', finish="length")] * 3)
    with pytest.raises(LlmError) as e:
        client(s).complete(request())
    assert e.value.kind == "output_format"
    assert len(s.requests) == 3


@pytest.mark.parametrize("status", [401, 403])
def test_auth_is_not_retried(status):
    s = Server(httpx.Response(status, json={"error": "no"}), ok('{"name": "x"}'))
    with pytest.raises(LlmError) as e:
        client(s).complete(request())
    assert e.value.kind == "auth"
    assert len(s.requests) == 1
    assert KEY not in str(e.value)


def test_429_waits_retry_after():
    sl = Sleeper()
    s = Server(httpx.Response(429, headers={"Retry-After": "3"}), ok('{"name": "x"}'))
    r = client(s, sleeper=sl).complete(request())
    assert r.value == {"name": "x"} and sl.slept == [3.0]


def test_429_exhausted_is_rate_limit():
    s = Server(*[httpx.Response(429)] * 3)
    with pytest.raises(LlmError) as e:
        client(s).complete(request())
    assert e.value.kind == "rate_limit"


def test_timeout():
    s = Server(*[httpx.ReadTimeout("t")] * 3)
    with pytest.raises(LlmError) as e:
        client(s).complete(request())
    assert e.value.kind == "timeout"


def test_5xx_is_unavailable():
    s = Server(*[httpx.Response(503)] * 3)
    with pytest.raises(LlmError) as e:
        client(s).complete(request())
    assert e.value.kind == "unavailable"


def test_json_schema_mode():
    s = Server(ok('{"name": "x"}'))
    client(s, mode="json_schema").complete(request())
    rf = s.requests[0]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == SCHEMA


def test_reasoning_effort_sent_when_set():
    s = Server(ok('{"name": "x"}'))
    client(s).complete(request(reasoning_effort="high"))
    assert s.requests[0]["reasoning_effort"] == "high"


def test_bad_mode_rejected():
    with pytest.raises(ValueError):
        OpenAiCompatibleClient(base_url="https://x", api_key=KEY, response_format="xml", max_retries=1,
                               http=httpx.Client())


def test_placeholders_are_unsupported():
    from a4w_clients.llm.placeholders import register
    from a4w_core.errors import ConfigError
    from a4w_core.registry import Registry

    reg = Registry()
    register(reg)
    with pytest.raises(ConfigError):
        reg.create("llm_client", "anthropic", None)
