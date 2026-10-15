"""ChatLlmClient 계약 시험 (Phase 4 설계 9절, 부록 B, AC-I7). tool calling이 있는 OpenAI 호환 /v1/chat/completions.

TDD red: 아직 구현이 없다(a4w_clients.llm.chat). 응답 모양은 OpenAI 호환 표준이다. 사내 엔드포인트의 실제 응답 녹화는
live 시험이 남기고(Phase 4 설계 17절), 입력 초과(context_length) 응답은 표준 모양(400, error.code)으로 가정한다 — 녹화 뒤 고친다.
"""

from __future__ import annotations

import json

import httpx
import pytest

from a4w_clients.llm.base import LlmClient, LlmError
from a4w_clients.llm.chat import ChatLlmClient, ChatMessage, ChatRequest, ToolCall, ToolSpec
from a4w_clients.llm.openai_compatible import OpenAiCompatibleClient

KEY = "sk-secret-key"
MODEL = "openai/gpt-oss-120b"

SEARCH_PAGES = ToolSpec(
    name="workfluence__search_pages", description="페이지 검색",
    parameters={"type": "object", "required": ["q"], "properties": {"q": {"type": "string"}}, "additionalProperties": False})


def reply(message: dict, finish="stop", usage=None):
    return {"id": "chatcmpl-1", "model": MODEL, "choices": [{"index": 0, "finish_reason": finish, "message": message}],
            "usage": usage or {"prompt_tokens": 812, "completion_tokens": 40,
                               "completion_tokens_details": {"reasoning_tokens": 25}}}


def tool_call(id_, name, arguments):
    return {"id": id_, "type": "function", "function": {"name": name, "arguments": arguments}}


class Server:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[dict] = []
        self.headers: list[httpx.Headers] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        self.headers.append(request.headers)
        assert request.url.path == "/v1/chat/completions"
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Sleeper:
    def __init__(self):
        self.slept: list[float] = []

    def __call__(self, s):
        self.slept.append(s)


def client(server, retries=2, sleeper=None):
    http = httpx.Client(transport=httpx.MockTransport(server))
    return OpenAiCompatibleClient(base_url="https://llm.test", api_key=KEY, response_format=None, max_retries=retries,
                                  http=http, sleep=sleeper or Sleeper())


def request(messages=None, tools=(SEARCH_PAGES,), **over):
    kw = dict(messages=tuple(messages or [ChatMessage("system", "너는 agent다"), ChatMessage("user", "회의록 찾아줘")]),
              tools=tuple(tools), model=MODEL, timeout_seconds=60, max_output_tokens=2048, temperature=0.2,
              reasoning_effort="low")
    kw.update(over)
    return ChatRequest(**kw)


def ok(message, **kw):
    return httpx.Response(200, json=reply(message, **kw))


def test_implements_both_interfaces_without_changing_the_old_one():
    c = client(Server())
    assert isinstance(c, ChatLlmClient) and isinstance(c, LlmClient)


def test_tool_call_response():
    s = Server(ok({"role": "assistant", "content": None,
                   "tool_calls": [tool_call("call_1", "workfluence__search_pages", '{"q": "회의록"}')]}, finish="tool_calls"))
    r = client(s).chat(request())
    assert r.finish_reason == "tool_calls"
    assert r.message.role == "assistant" and r.message.content is None
    assert r.message.tool_calls == (ToolCall(id="call_1", name="workfluence__search_pages", arguments='{"q": "회의록"}'),)
    assert (r.usage.prompt_tokens, r.usage.completion_tokens, r.usage.reasoning_tokens) == (812, 40, 25)
    assert r.model == MODEL and r.attempts == 1


def test_request_body_uses_openai_function_format():
    s = Server(ok({"role": "assistant", "content": "네"}))
    client(s).chat(request())
    body = s.requests[0]
    assert body["model"] == MODEL and body["max_tokens"] == 2048
    assert body["temperature"] == 0.2 and body["reasoning_effort"] == "low"
    assert body["tools"] == [{"type": "function", "function": {
        "name": "workfluence__search_pages", "description": "페이지 검색", "parameters": SEARCH_PAGES.parameters}}]
    assert body["messages"] == [{"role": "system", "content": "너는 agent다"}, {"role": "user", "content": "회의록 찾아줘"}]
    assert "response_format" not in body          # chat은 json 모드를 쓰지 않는다
    assert s.headers[0]["Authorization"] == f"Bearer {KEY}"


def test_final_answer():
    r = client(Server(ok({"role": "assistant", "content": "회의록 페이지는 '10월 주간회의'입니다."}))).chat(request())
    assert r.finish_reason == "stop" and r.message.tool_calls == ()
    assert r.message.content == "회의록 페이지는 '10월 주간회의'입니다."


def test_tool_loop_messages_are_serialized_in_order():
    """assistant(tool_calls) → tool(tool_call_id) → 다음 차례. 이전 차례를 그대로 되돌려 보낸다."""
    s = Server(ok({"role": "assistant", "content": "찾았습니다"}))
    msgs = [ChatMessage("system", "s"), ChatMessage("user", "회의록 찾아줘"),
            ChatMessage("assistant", None, tool_calls=(ToolCall("call_1", "workfluence__search_pages", '{"q":"회의록"}'),)),
            ChatMessage("tool", '<<data>>{"items":[]}<<end>>', tool_call_id="call_1")]
    client(s).chat(request(messages=msgs))
    sent = s.requests[0]["messages"]
    assert sent[2] == {"role": "assistant", "content": None,
                       "tool_calls": [tool_call("call_1", "workfluence__search_pages", '{"q":"회의록"}')]}
    assert sent[3] == {"role": "tool", "tool_call_id": "call_1", "content": '<<data>>{"items":[]}<<end>>'}


def test_parallel_tool_calls_keep_order():
    calls = [tool_call("a", "workfluence__search_pages", '{"q":"1"}'), tool_call("b", "workfluence__search_pages", '{"q":"2"}')]
    r = client(Server(ok({"role": "assistant", "content": None, "tool_calls": calls}, finish="tool_calls"))).chat(request())
    assert [c.id for c in r.message.tool_calls] == ["a", "b"]


def test_arguments_that_are_not_json_are_returned_raw():
    """판단은 engine 몫이다. client는 모델이 낸 문자열을 그대로 돌려준다."""
    bad = '{"q": "회의록"'
    r = client(Server(ok({"role": "assistant", "content": None, "tool_calls": [tool_call("c", "x", bad)]},
                         finish="tool_calls"))).chat(request())
    assert r.message.tool_calls[0].arguments == bad


def test_no_tools_means_no_tools_key():
    s = Server(ok({"role": "assistant", "content": "할 수 없습니다"}))
    client(s).chat(request(tools=()))
    assert "tools" not in s.requests[0]


def test_optional_sampling_fields_are_omitted_when_none():
    s = Server(ok({"role": "assistant", "content": "네"}))
    client(s).chat(request(temperature=None, reasoning_effort=None))
    assert "temperature" not in s.requests[0] and "reasoning_effort" not in s.requests[0]


def test_finish_reason_length_is_returned_not_retried():
    s = Server(ok({"role": "assistant", "content": "잘린 답…"}, finish="length"))
    r = client(s).chat(request())
    assert r.finish_reason == "length" and len(s.requests) == 1


def test_reasoning_content_is_never_read_or_returned():
    r = client(Server(ok({"role": "assistant", "content": "답", "reasoning_content": "비밀 사고"}))).chat(request())
    assert "비밀 사고" not in repr(r)


def test_missing_usage_is_zero():
    resp = httpx.Response(200, json={"model": MODEL, "choices": [{"finish_reason": "stop",
                                                                   "message": {"role": "assistant", "content": "네"}}]})
    r = client(Server(resp)).chat(request())
    assert (r.usage.prompt_tokens, r.usage.completion_tokens, r.usage.reasoning_tokens) == (0, 0, 0)


# ---- 오류 분류 (기존 complete()와 같은 규칙) ----

@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure_is_not_retried(status):
    s = Server(httpx.Response(status, json={"error": {"message": "bad key"}}))
    with pytest.raises(LlmError) as e:
        client(s).chat(request())
    assert e.value.kind == "auth" and len(s.requests) == 1
    assert KEY not in str(e.value)


def test_rate_limit_retries_and_honors_retry_after():
    sleeper = Sleeper()
    s = Server(httpx.Response(429, headers={"Retry-After": "3"}, json={}), ok({"role": "assistant", "content": "네"}))
    r = client(s, sleeper=sleeper).chat(request())
    assert r.attempts == 2 and sleeper.slept == [3.0]


def test_rate_limit_exhausted():
    s = Server(*[httpx.Response(429, json={}) for _ in range(3)])
    with pytest.raises(LlmError) as e:
        client(s, retries=2).chat(request())
    assert e.value.kind == "rate_limit" and e.value.attempts == 3


def test_timeout_then_success_and_timeout_exhausted():
    s = Server(httpx.ReadTimeout("slow"), ok({"role": "assistant", "content": "네"}))
    assert client(s).chat(request()).attempts == 2
    s = Server(*[httpx.ReadTimeout("slow") for _ in range(3)])
    with pytest.raises(LlmError) as e:
        client(s).chat(request())
    assert e.value.kind == "timeout"


def test_connection_error_and_5xx_are_unavailable():
    with pytest.raises(LlmError) as e:
        client(Server(*[httpx.ConnectError("down") for _ in range(3)])).chat(request())
    assert e.value.kind == "unavailable"
    with pytest.raises(LlmError) as e:
        client(Server(*[httpx.Response(503, text="busy") for _ in range(3)])).chat(request())
    assert e.value.kind == "unavailable"


@pytest.mark.parametrize("body", [{"nope": 1}, {"choices": []}, {"choices": [{"message": {}}]}])
def test_response_not_in_chat_completion_shape(body):
    with pytest.raises(LlmError) as e:
        client(Server(httpx.Response(200, json=body))).chat(request())
    assert e.value.kind == "output_format"


def test_non_json_body():
    with pytest.raises(LlmError) as e:
        client(Server(httpx.Response(200, text="<html>proxy error</html>"))).chat(request())
    assert e.value.kind == "output_format"


def test_context_length_exceeded_is_its_own_kind_and_not_retried():
    """입력이 모델 창을 넘으면 같은 요청을 되풀이해도 소용없다. engine이 이력을 줄여 다시 부른다(설계 6.7)."""
    body = {"error": {"code": "context_length_exceeded", "type": "invalid_request_error",
                      "message": "This model's maximum context length is 131072 tokens. However, you requested 140000 tokens"}}
    s = Server(httpx.Response(400, json=body))
    with pytest.raises(LlmError) as e:
        client(s).chat(request())
    assert e.value.kind == "context_length" and len(s.requests) == 1


def test_other_400_is_not_mistaken_for_context_length():
    s = Server(*[httpx.Response(400, json={"error": {"code": "invalid_request_error", "message": "tools[0] is invalid"}})
                 for _ in range(3)])
    with pytest.raises(LlmError) as e:
        client(s).chat(request())
    assert e.value.kind != "context_length"


def test_key_is_not_in_repr_or_logs(caplog):
    caplog.set_level("DEBUG")
    c = client(Server(ok({"role": "assistant", "content": "네"})))
    c.chat(request())
    assert KEY not in repr(c) and KEY not in caplog.text
    assert "회의록 찾아줘" not in caplog.text            # 요청 본문은 로그에 남기지 않는다 (NFR-I03)


def test_registry_creates_chat_client_without_json_mode_settings():
    from a4w_clients.llm import placeholders
    from a4w_core.config import Settings
    from a4w_core.registry import Registry

    reg = Registry()
    placeholders.register(reg)
    env = Settings({"LLM_BASE_URL": "https://llm.test", "LLM_API_KEY": KEY, "LLM_MAX_RETRIES": "2"}, origin="test")
    created = reg.create("chat_llm_client", "openai_compatible", (env, env))
    assert isinstance(created, ChatLlmClient)
    with pytest.raises(Exception):                     # 자리만 있는 provider
        reg.create("chat_llm_client", "anthropic", (env, env))
