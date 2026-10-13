"""공통 실행기: 단순 tool과 상위 tool."""

from __future__ import annotations

import json

import httpx
import pytest

from a4w_tools.call_policy import ConfigCallPolicy
from a4w_tools.executor import ToolExecutor
from a4w_tools.interfaces import GenericErrorInterpreter
from a4w_tools.target_http import TargetHttpClient

TOKEN = "secret-token-value"


class NoSleepClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.now += s


class Recorder:
    """요청을 기록하고, (METHOD, path) → 응답 함수로 답한다."""

    def __init__(self, routes):
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        handler = self.routes.get(key)
        if handler is None:
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "없음", "requestId": "rq-404"}})
        return handler(request)


def make_executor(routes):
    rec = Recorder(routes)
    client = httpx.Client(transport=httpx.MockTransport(rec))
    http = TargetHttpClient(base_url="https://wf.test/api/v1", token=TOKEN, client=client)
    policy = ConfigCallPolicy(
        read_per_minute=100, write_per_minute=100, max_attempts=2, max_retry_wait_seconds=5, clock=NoSleepClock()
    )
    return ToolExecutor(http=http, policy=policy, interpreter=GenericErrorInterpreter()), rec


def ok(body, status=200):
    return lambda req: httpx.Response(status, json=body)


def test_input_invalid_makes_no_call(op_tool):
    ex, rec = make_executor({})
    result = ex.execute(op_tool, {"pageId": "p"})
    assert not result.ok
    assert result.error.code == "INPUT_INVALID"
    assert rec.requests == []


def test_success_shapes_response_and_sends_auth(op_tool):
    ex, rec = make_executor(
        {("POST", "/api/v1/pages/p1/comments"): ok({"id": "c1", "createdAt": "t", "noise": 1}, 201)}
    )
    result = ex.execute(op_tool, {"pageId": "p1", "body": "hi"})
    assert result.ok
    assert result.data == {"id": "c1", "createdAt": "t"}
    req = rec.requests[0]
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"
    assert json.loads(req.content) == {"body": "hi", "format": "markdown"}
    assert result.meta["status"] == 201


def test_target_error_is_interpreted_without_token(op_tool):
    body = {"error": {"code": "VERSION_CONFLICT", "message": "충돌", "requestId": "rq-1"}}
    ex, _ = make_executor({("POST", "/api/v1/pages/p1/comments"): ok(body, 409)})
    result = ex.execute(op_tool, {"pageId": "p1", "body": "hi"})
    assert not result.ok
    assert result.error.code == "TARGET_ERROR"
    assert result.error.http_status == 409
    assert TOKEN not in json.dumps(result.to_dict(), ensure_ascii=False)


def test_status_outside_success_list_is_error(op_tool):
    ex, _ = make_executor({("POST", "/api/v1/pages/p1/comments"): ok({}, 202)})
    result = ex.execute(op_tool, {"pageId": "p1", "body": "hi"})
    assert not result.ok


def test_non_json_response_is_returned_as_text(search_tool):
    ex, _ = make_executor({("GET", "/api/v1/search"): lambda r: httpx.Response(200, text="plain text")})
    result = ex.execute(search_tool, {"q": "a"})
    assert result.ok
    assert result.data == "plain text"


def test_to_dict_shape(search_tool):
    ex, _ = make_executor({("GET", "/api/v1/search"): ok({"items": []})})
    d = ex.execute(search_tool, {"q": "ab"}).to_dict()
    assert set(d) == {"ok", "data", "truncated", "error", "meta"}


def test_unknown_handler_is_unsupported(op_tool):
    tool = {k: v for k, v in op_tool.items() if k not in ("http", "response", "source")}
    tool["kind"] = "handler"
    tool["handler"] = "nothing"
    ex, _ = make_executor({})
    result = ex.execute(tool, {"pageId": "p", "body": "x"})
    assert result.error.code == "UNSUPPORTED"


# ── 상위 tool


def toolset(op_tool, search_tool):
    return {op_tool["name"]: op_tool, search_tool["name"]: search_tool}


def test_composite_runs_steps_in_order(op_tool, search_tool, composite_tool):
    ex, rec = make_executor(
        {
            ("GET", "/api/v1/search"): ok({"items": [{"id": "p9", "title": "회의록"}]}),
            ("POST", "/api/v1/pages/p9/comments"): ok({"id": "c1", "createdAt": "t"}, 201),
        }
    )
    result = ex.execute(composite_tool, {"title": "회의록", "body": "좋아요"}, toolset=toolset(op_tool, search_tool))
    assert result.ok
    assert result.data == {"id": "c1", "createdAt": "t"}
    assert "space" not in rec.requests[0].url.params  # 비운 선택 칸은 보내지 않는다
    assert [s["id"] for s in result.meta["steps"]] == ["find", "comment"]


def test_composite_not_found(op_tool, search_tool, composite_tool):
    ex, _ = make_executor({("GET", "/api/v1/search"): ok({"items": []})})
    result = ex.execute(composite_tool, {"title": "x", "body": "y"}, toolset=toolset(op_tool, search_tool))
    assert result.error.code == "NOT_FOUND"
    assert result.error.step == "find"


def test_composite_ambiguous_lists_candidates(op_tool, search_tool, composite_tool):
    items = [{"id": f"p{i}", "title": "t"} for i in range(3)]
    ex, _ = make_executor({("GET", "/api/v1/search"): ok({"items": items})})
    result = ex.execute(composite_tool, {"title": "x", "body": "y"}, toolset=toolset(op_tool, search_tool))
    assert result.error.code == "AMBIGUOUS"
    assert len(result.data["candidates"]) == 3


def test_composite_step_failure_keeps_target_details(op_tool, search_tool, composite_tool):
    err = {"error": {"code": "FORBIDDEN", "message": "권한 없음", "requestId": "rq-9"}}
    ex, _ = make_executor(
        {
            ("GET", "/api/v1/search"): ok({"items": [{"id": "p9", "title": "t"}]}),
            ("POST", "/api/v1/pages/p9/comments"): ok(err, 403),
        }
    )
    result = ex.execute(composite_tool, {"title": "x", "body": "y"}, toolset=toolset(op_tool, search_tool))
    assert result.error.code == "STEP_FAILED"
    assert result.error.step == "comment"
    assert result.error.http_status == 403


def test_composite_without_toolset_is_unsupported(composite_tool):
    ex, _ = make_executor({})
    result = ex.execute(composite_tool, {"title": "x", "body": "y"})
    assert result.error.code == "UNSUPPORTED"


def test_composite_input_checked_first(op_tool, search_tool, composite_tool):
    ex, rec = make_executor({})
    result = ex.execute(composite_tool, {"title": "x"}, toolset=toolset(op_tool, search_tool))
    assert result.error.code == "INPUT_INVALID"
    assert rec.requests == []


@pytest.mark.parametrize("bad", ["", "no-scheme"])
def test_target_client_rejects_bad_base_url(bad):
    with pytest.raises(ValueError):
        TargetHttpClient(base_url=bad, token=TOKEN, client=httpx.Client())


@pytest.mark.parametrize("base,prefix,expected", [
    ("https://wf:8444", "/api/v1", "https://wf:8444/api/v1"),
    ("https://wf:8444/", "api/v1/", "https://wf:8444/api/v1"),
    ("https://wf:8444/api/v1", "/", "https://wf:8444/api/v1"),
])
def test_from_settings_joins_path_prefix(base, prefix, expected):
    from a4w_core.config import Settings, TargetConfig

    cfg = TargetConfig("wf", {"TARGET_ADAPTER": "x", "TLS_VERIFY": "false", "CALL_TIMEOUT_SECONDS": "5",
                              "TARGET_API_PATH_PREFIX": prefix})
    secrets = Settings({"TARGET_API_BASE_URL": base, "TARGET_API_TOKEN": TOKEN}, origin="s")
    assert TargetHttpClient.from_settings(cfg, secrets).base_url == expected


def test_context_flows_to_composite_steps(op_tool, search_tool, composite_tool):
    search_tool["http"]["fixed"].append({"in": "query", "name": "space", "context": "homeSpace"})
    ex, rec = make_executor(
        {
            ("GET", "/api/v1/search"): ok({"items": [{"id": "p9", "title": "t"}]}),
            ("POST", "/api/v1/pages/p9/comments"): ok({"id": "c1", "createdAt": "t"}, 201),
        }
    )
    result = ex.execute(composite_tool, {"title": "t", "body": "b"}, toolset=toolset(op_tool, search_tool),
                        context={"homeSpace": "home"})
    assert result.ok
    assert rec.requests[0].url.params["space"] == "home"


def test_context_missing_is_reported(search_tool):
    search_tool["http"]["fixed"].append({"in": "query", "name": "space", "context": "homeSpace"})
    ex, rec = make_executor({})
    result = ex.execute(search_tool, {"q": "a"})
    assert result.error.code == "CONTEXT_MISSING" and rec.requests == []
