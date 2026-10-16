"""REST 채널 (설계 5절, 부록 B.8 H1~H3·H14~H17, AC-I1). 실제 FastAPI 앱 + 실제 SQLite + 가짜 engine."""

from __future__ import annotations

import anyio
import httpx
import pytest

from a4w_inference.channels.rest.app import RestContext, create_app
from a4w_inference.engine.models import RunFailed, Usage
from a4w_inference.runner import RequestRunner
from a4w_inference.storage.sqlite_db import SqliteInferenceDb
from tests.fakes import FakeEngine, action, result

pytestmark = pytest.mark.anyio
TOKEN = "tok-SECRET-123"


def build(tmp_path, engine, *, auth="none", token=None, limits=None):
    db = SqliteInferenceDb(tmp_path / "i.db")
    ids = iter(f"r-{i:04d}" for i in range(1, 1000))
    runner = RequestRunner(engine=engine, db=db, agent_limits=limits or {"aiagent": 2}, text_max_chars=500, requester_max_chars=40,
                           now_iso=lambda: "2026-10-15T00:30:00Z", new_id=lambda: next(ids))
    app = create_app(RestContext(runner=runner, auth_mode=auth, api_token=token,
                                 health=lambda: {"aiagent": {"mcp": "up", "tools": 30}}))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://inference.test")
    return client, runner, db


BODY = {"agentId": "aiagent", "text": "회의록 페이지 찾아줘", "requester": "svc-workfluence"}


async def test_H1_post_returns_202_then_get_shows_the_finished_result(tmp_path):
    a = action("search_pages")
    client, runner, _ = build(tmp_path, FakeEngine(result("'10월 주간회의 회의록'이 있습니다.", actions=[a])))
    async with client:
        r = await client.post("/v1/requests", json={**BODY, "conversationId": "c-1"})
        assert r.status_code == 202
        body = r.json()
        assert body["requestId"] == "r-0001" and body["status"] == "running" and body["conversationId"] == "c-1"
        await runner.drain()
        g = (await client.get("/v1/requests/r-0001")).json()
    assert g["status"] == "done" and g["complete"] is True and g["stopReason"] is None and g["error"] is None
    assert g["answer"] == "'10월 주간회의 회의록'이 있습니다." and g["agentId"] == "aiagent" and g["conversationId"] == "c-1"
    assert g["actions"] == [{"tool": "workfluence__search_pages", "revision": "r-1", "ok": True, "errorCode": None,
                             "targetRequestId": "wf-req-0", "write": False, "args": {}, "refs": {}}]
    assert g["usage"]["promptTokens"] == 1200 and g["usage"]["steps"] == 2 and g["usage"]["toolCalls"] == 1
    assert g["usage"]["lastInputTokens"] == 1200
    assert {"requestId", "elapsedMs", "createdAt", "finishedAt"} <= set(g)


async def test_get_while_running(tmp_path):
    gate = anyio.Event()
    client, runner, _ = build(tmp_path, FakeEngine(gate=gate))
    async with client:
        await client.post("/v1/requests", json=BODY)
        g = (await client.get("/v1/requests/r-0001")).json()
        gate.set(); await runner.drain()
    assert g["status"] == "running" and g["answer"] is None and g["complete"] is None and g["finishedAt"] is None


@pytest.mark.parametrize("patch", [{"text": ""}, {"text": "   "}, {"text": "가" * 501}, {"requester": ""}, {"requester": "r" * 41},
                                   {"conversationId": "한글"}, {"idempotencyKey": "a b"}, {"agentId": None}, {"text": 123}])
async def test_H2_validation_is_400_with_the_common_error_shape(tmp_path, patch):
    client, _, _ = build(tmp_path, FakeEngine())
    async with client:
        r = await client.post("/v1/requests", json={**BODY, **patch})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR" and r.json()["error"]["message"]


async def test_H2_missing_fields_and_bad_json_are_400(tmp_path):
    client, _, _ = build(tmp_path, FakeEngine())
    async with client:
        assert (await client.post("/v1/requests", json={"agentId": "aiagent"})).status_code == 400
        assert (await client.post("/v1/requests", content=b"{not json", headers={"content-type": "application/json"})).status_code == 400


async def test_H2_unknown_agent_is_404(tmp_path):
    client, _, _ = build(tmp_path, FakeEngine())
    async with client:
        r = await client.post("/v1/requests", json={**BODY, "agentId": "ghost"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "AGENT_NOT_FOUND"


async def test_H3_bearer_auth_on_post_and_get_and_the_token_is_never_echoed(tmp_path):
    client, runner, _ = build(tmp_path, FakeEngine(), auth="bearer", token=TOKEN)
    async with client:
        none = await client.post("/v1/requests", json=BODY)
        wrong = await client.post("/v1/requests", json=BODY, headers={"Authorization": "Bearer nope"})
        scheme = await client.post("/v1/requests", json=BODY, headers={"Authorization": TOKEN})
        ok_ = await client.post("/v1/requests", json=BODY, headers={"Authorization": f"Bearer {TOKEN}"})
        await runner.drain()
        get_none = await client.get("/v1/requests/r-0001")
        get_ok = await client.get("/v1/requests/r-0001", headers={"Authorization": f"Bearer {TOKEN}"})
    assert [none.status_code, wrong.status_code, scheme.status_code, ok_.status_code] == [401, 401, 401, 202]
    assert get_none.status_code == 401 and get_ok.status_code == 200
    for r in (none, wrong, scheme, get_none):
        assert r.json()["error"]["code"] == "UNAUTHORIZED" and TOKEN not in r.text and "nope" not in r.text


async def test_H3_auth_none_needs_no_header_and_health_is_always_open(tmp_path):
    client, _, _ = build(tmp_path, FakeEngine(), auth="bearer", token=TOKEN)
    async with client:
        h = await client.get("/health")
    assert h.status_code == 200 and h.json() == {"status": "ok", "agents": {"aiagent": {"mcp": "up", "tools": 30}}}


async def test_H4_idempotent_replay_is_200_with_the_same_request_id(tmp_path):
    engine = FakeEngine()
    client, runner, _ = build(tmp_path, engine)
    async with client:
        a = await client.post("/v1/requests", json={**BODY, "idempotencyKey": "k-1"})
        await runner.drain()
        b = await client.post("/v1/requests", json={**BODY, "idempotencyKey": "k-1"})
        c = await client.post("/v1/requests", json={**BODY, "text": "다른 요청", "idempotencyKey": "k-1"})
    assert a.status_code == 202 and b.status_code == 200 and b.json()["requestId"] == a.json()["requestId"]
    assert b.json()["status"] == "done"
    assert c.status_code == 409 and c.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert len(engine.seen) == 1


async def test_H7_busy_conversation_is_409_and_H9_busy_agent_is_429(tmp_path):
    gate = anyio.Event()
    client, runner, _ = build(tmp_path, FakeEngine(gate=gate), limits={"aiagent": 2})
    async with client:
        await client.post("/v1/requests", json={**BODY, "conversationId": "c-1"})
        busy = await client.post("/v1/requests", json={**BODY, "text": "다음", "conversationId": "c-1"})
        await client.post("/v1/requests", json={**BODY, "text": "둘째", "conversationId": "c-2"})
        full = await client.post("/v1/requests", json={**BODY, "text": "셋째", "conversationId": "c-3"})
        gate.set(); await runner.drain()
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "CONVERSATION_BUSY"
    assert full.status_code == 429 and full.json()["error"]["code"] == "AGENT_BUSY"


async def test_H14_unknown_request_is_404(tmp_path):
    client, _, _ = build(tmp_path, FakeEngine())
    async with client:
        r = await client.get("/v1/requests/r-없음")
    assert r.status_code == 404 and r.json()["error"]["code"] == "REQUEST_NOT_FOUND"


async def test_failed_request_shape(tmp_path):
    w = action("create_page", write=True)
    client, runner, _ = build(tmp_path, FakeEngine(RunFailed("MCP_UNAVAILABLE", "agent의 MCP server가 다시 뜨는 중이다", actions=(w,),
                                                            usage=Usage(steps=2, write_calls=1))))
    async with client:
        await client.post("/v1/requests", json=BODY)
        await runner.drain()
        g = (await client.get("/v1/requests/r-0001")).json()
    assert g["status"] == "failed" and g["answer"] is None and g["complete"] is None
    assert g["error"] == {"code": "MCP_UNAVAILABLE", "message": "agent의 MCP server가 다시 뜨는 중이다"}
    assert len(g["actions"]) == 1 and g["actions"][0]["write"] is True            # 쓴 것은 보인다


async def test_H17_limit_stop_shape(tmp_path):
    client, runner, _ = build(tmp_path, FakeEngine(result(None, complete=False, stop_reason="LIMIT_STEPS", actions=[action("list_pages")])))
    async with client:
        await client.post("/v1/requests", json=BODY)
        await runner.drain()
        g = (await client.get("/v1/requests/r-0001")).json()
    assert (g["status"], g["complete"], g["stopReason"], g["answer"], g["error"]) == ("done", False, "LIMIT_STEPS", None, None)
    assert len(g["actions"]) == 1


async def test_H16_openapi_and_unknown_routes_follow_the_common_error_shape(tmp_path):
    client, _, _ = build(tmp_path, FakeEngine())
    async with client:
        spec = (await client.get("/openapi.json")).json()
        nf = await client.get("/v1/nothing")
        bad_method = await client.put("/v1/requests")
    assert "/v1/requests" in spec["paths"] and "/v1/requests/{requestId}" in spec["paths"]
    assert nf.status_code == 404 and "error" in nf.json()
    assert bad_method.status_code == 405 and "error" in bad_method.json()


async def test_the_response_never_contains_the_requester_text_of_other_requests(tmp_path):
    client, runner, _ = build(tmp_path, FakeEngine())
    async with client:
        await client.post("/v1/requests", json={**BODY, "text": "첫 번째 비밀", "conversationId": "c-1"})
        await runner.drain()
        await client.post("/v1/requests", json={**BODY, "text": "두 번째", "conversationId": "c-1"})
        await runner.drain()
        g = (await client.get("/v1/requests/r-0002")).text
    assert "첫 번째 비밀" not in g                         # 조회 응답에는 이력(앞선 요청 text)이 섞이지 않는다
