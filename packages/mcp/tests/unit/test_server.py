"""MCP server (Phase 3 설계 7절, AC-T3~T8). SDK 2.x 메모리 전송으로 두 프로토콜 판을 다 본다."""

from __future__ import annotations

import json

import anyio
import httpx
import mcp.types as t
import pytest
from mcp.client.client import Client
from mcp.shared.exceptions import MCPError

from a4w_mcp.catalog import ServingCatalog
from a4w_mcp.notifier import ChangeNotifier
from a4w_mcp.refresher import Refresher
from a4w_mcp.runner import DirectToolRunner
from a4w_mcp.server import build_server
from a4w_mcp.snapshot import Exposure, Snapshot
from a4w_mcp.source import ManufactureToolsetSource
from a4w_tools.call_policy import ConfigCallPolicy
from a4w_tools.executor import ToolExecutor
from a4w_tools.interfaces import GenericErrorInterpreter
from a4w_tools.target_http import TargetHttpClient

from ..fakes import SPACE, TARGET, TOKEN, FakeManufacture, FakeTarget, bundle, search_pages

pytestmark = pytest.mark.anyio
MODES = ["2026-07-28", "legacy"]


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Stack:
    def __init__(self, *, context=None, scopes=None, routes=None):
        self.manufacture = FakeManufacture(bundle())
        self.target = FakeTarget(routes)
        self.catalog = ServingCatalog(targets=[TARGET], clock=lambda: "2026-10-14T00:00:00Z")
        self.notifier = ChangeNotifier()
        ctx = {"homeSpace": SPACE} if context is None else context
        expose = Exposure(scopes=scopes, max_risk="danger", groups=None, separator="__", name_max=64, context=ctx)
        src = ManufactureToolsetSource(base_url="http://m:1", token=None, timeout=5,
                                       transport=httpx.MockTransport(self.manufacture))
        self.refresher = Refresher(source=src, exposures={TARGET: expose}, catalog=self.catalog,
                                   notifier=self.notifier, refresh_seconds=60, retry_seconds=10)
        http = TargetHttpClient(base_url="https://wf.test/api/v1", token=TOKEN,
                                client=httpx.Client(transport=httpx.MockTransport(self.target)))
        policy = ConfigCallPolicy(read_per_minute=100, write_per_minute=100, max_attempts=1, max_retry_wait_seconds=1)
        executor = ToolExecutor(http=http, policy=policy, interpreter=GenericErrorInterpreter())
        self.runner = DirectToolRunner(executors={TARGET: executor}, contexts={TARGET: ctx})
        self.server = build_server(name="a4w-mcp", version="t", agent_id="aiagent", catalog=self.catalog,
                                   runner=self.runner, refresher=self.refresher, notifier=self.notifier,
                                   max_concurrent=2, scopes={TARGET: ["read", "write"]})


async def started(**kw):
    s = Stack(**kw)
    await s.refresher.refresh_once()
    return s


def data(result: t.CallToolResult):
    assert json.loads(result.content[0].text) == result.structured_content
    return result.structured_content


@pytest.mark.parametrize("mode", MODES)
async def test_list_tools_shape(mode):
    s = await started()
    async with Client(s.server, mode=mode) as c:
        tools = {x.name: x for x in (await c.list_tools()).tools}
    assert set(tools) == {"wf__search_pages", "wf__add_comment", "wf__delete_page", "wf__comment_on_page_by_title"}
    sp = tools["wf__search_pages"]
    assert sp.title == "search_pages 제목" and sp.description == "search_pages 설명"
    assert sp.input_schema == search_pages()["inputSchema"]
    dumped = json.dumps(sp.model_dump(by_alias=True), ensure_ascii=False)
    assert "homeSpace" not in dumped and '"http"' not in dumped and "/search" not in dumped
    assert sp.meta == {"a4w/targetId": TARGET, "a4w/name": "search_pages", "a4w/revision": "r-000000000001",
                       "a4w/risk": "read", "a4w/toolVersion": "tv-000001-aaaaaaaa", "a4w/group": "read"}
    ann = {n: (x.annotations.read_only_hint, x.annotations.destructive_hint) for n, x in tools.items()}
    assert ann["wf__search_pages"] == (True, False)
    assert ann["wf__add_comment"] == (False, False)
    assert ann["wf__delete_page"] == (False, True)


@pytest.mark.parametrize("mode", MODES)
async def test_call_read_tool_with_context(mode):
    s = await started()
    async with Client(s.server, mode=mode) as c:
        r = await c.call_tool("wf__search_pages", {"q": "회의록"})
    assert r.is_error is False
    assert data(r) == {"data": {"items": [{"pageId": "p1", "title": "회의록"}]}, "truncated": False}
    (req,) = s.target.requests
    assert req.url.params["space"] == SPACE and req.headers["authorization"] == f"Bearer {TOKEN}"
    assert r.meta["a4w/revision"] == "r-000000000001"


async def test_call_log_has_no_token_or_body(caplog):
    import logging

    s = await started()
    with caplog.at_level(logging.INFO, logger="a4w_mcp"):
        async with Client(s.server, mode="2026-07-28") as c:
            await c.call_tool("wf__add_comment", {"pageId": "p1", "body": "비밀스러운 본문"})
    lines = [r.getMessage() for r in caplog.records if r.name.startswith("a4w_mcp")]
    assert any("tool=wf__add_comment" in m and "ok=true" in m for m in lines)
    assert TOKEN not in caplog.text and "비밀스러운 본문" not in caplog.text


async def test_composite_runs_steps():
    s = await started()
    async with Client(s.server, mode="2026-07-28") as c:
        r = await c.call_tool("wf__comment_on_page_by_title", {"title": "회의록", "body": "확인"})
    assert r.is_error is False and data(r)["data"]["id"] == "c1"
    assert [q.method for q in s.target.requests] == ["GET", "POST"]


async def test_input_invalid_is_tool_error_without_call():
    s = await started()
    async with Client(s.server, mode="2026-07-28") as c:
        r = await c.call_tool("wf__add_comment", {"pageId": "p1"})
    assert r.is_error is True
    err = data(r)["error"]
    assert err["code"] == "INPUT_INVALID" and err["hint"]
    assert s.target.requests == []


async def test_target_error_keeps_request_id():
    s = await started()
    async with Client(s.server, mode="2026-07-28") as c:
        r = await c.call_tool("wf__add_comment", {"pageId": "nope", "body": "x"})
    assert r.is_error is True and data(r)["error"]["httpStatus"] == 404


async def test_context_missing_makes_no_call():
    s = await started(context={})
    async with Client(s.server, mode="2026-07-28") as c:
        r = await c.call_tool("wf__search_pages", {"q": "x"})
    assert r.is_error is True and data(r)["error"]["code"] == "CONTEXT_MISSING"
    assert s.target.requests == []


@pytest.mark.parametrize("mode", MODES)
async def test_unknown_and_hidden_tools_are_protocol_errors(mode):
    s = await started(scopes=frozenset({"read"}))
    async with Client(s.server, mode=mode) as c:
        names = {x.name for x in (await c.list_tools()).tools}
        assert names == {"wf__search_pages"}
        for name in ("wf__add_comment", "wf__nothing"):
            with pytest.raises(MCPError):
                await c.call_tool(name, {"pageId": "p1", "body": "x"})
    assert s.target.requests == []


async def test_revision_mismatch_refetches_then_rejects():
    s = await started()
    async with Client(s.server, mode="2026-07-28") as c:
        before = len(s.manufacture.requests)
        r = await c.call_tool("wf__search_pages", {"q": "x"}, meta={"a4w/expectedRevision": "r-0000000000ff"})
    assert r.is_error is True
    err = data(r)["error"]
    assert err["code"] == "REVISION_MISMATCH"
    assert err["details"] == {"expected": "r-0000000000ff", "current": "r-000000000001"}
    assert len(s.manufacture.requests) > before  # 한 번 다시 확인했다
    assert s.target.requests == []


async def test_revision_mismatch_resolved_by_new_version():
    s = await started()
    s.manufacture.publish(bundle([search_pages("r-0000000000ff")], version="tv-000002-bbbbbbbb"))
    async with Client(s.server, mode="2026-07-28") as c:
        r = await c.call_tool("wf__search_pages", {"q": "x"}, meta={"a4w/expectedRevision": "r-0000000000ff"})
    assert r.is_error is False and r.meta["a4w/revision"] == "r-0000000000ff"


async def test_matching_revision_runs():
    s = await started()
    async with Client(s.server, mode="2026-07-28") as c:
        r = await c.call_tool("wf__search_pages", {"q": "x"}, meta={"a4w/expectedRevision": "r-000000000001"})
    assert r.is_error is False


async def test_call_in_flight_keeps_its_snapshot():
    holder = {}

    def search(req):
        holder["stack"].catalog.replace(Snapshot(), [])  # 실행 도중 묶음이 바뀐다
        return httpx.Response(200, json={"items": []})

    s = await started(routes={("GET", "/search"): search})
    holder["stack"] = s
    async with Client(s.server, mode="2026-07-28") as c:
        r = await c.call_tool("wf__search_pages", {"q": "x"})
        assert r.is_error is False and r.meta["a4w/toolVersion"] == "tv-000001-aaaaaaaa"
        assert (await c.list_tools(cache_mode="refresh")).tools == []


async def test_modern_listen_gets_change_event():
    s = await started()
    async with Client(s.server, mode="2026-07-28") as c:
        async with c.listen(tools_list_changed=True) as sub:
            s.manufacture.publish(bundle([search_pages()], version="tv-000002-bbbbbbbb"))
            await s.refresher.refresh_once()
            with anyio.fail_after(2):
                async for _ in sub:
                    break
        assert {x.name for x in (await c.list_tools()).tools} == {"wf__search_pages"}


async def test_legacy_session_gets_list_changed_notification():
    s = await started()
    got = []

    async def on_message(m):
        got.append(type(getattr(m, "root", m)).__name__)

    async with Client(s.server, mode="legacy", message_handler=on_message) as c:
        await c.list_tools()  # session을 알게 된다
        s.manufacture.publish(bundle([search_pages()], version="tv-000002-bbbbbbbb"))
        await s.refresher.refresh_once()
        with anyio.fail_after(2):
            while "ToolListChangedNotification" not in got:
                await anyio.sleep(0.01)
        assert {x.name for x in (await c.list_tools()).tools} == {"wf__search_pages"}


@pytest.mark.parametrize("mode", MODES)
async def test_status_resource(mode):
    s = await started()
    async with Client(s.server, mode=mode) as c:
        res = (await c.list_resources()).resources
        assert [str(r.uri) for r in res] == ["a4w://status"]
        body = json.loads((await c.read_resource("a4w://status")).contents[0].text)
    assert body["agentId"] == "aiagent" and body["scopes"] == {TARGET: ["read", "write"]}
    assert body["targets"][TARGET]["toolVersion"] == "tv-000001-aaaaaaaa" and body["targets"][TARGET]["served"] == 4
    assert TOKEN not in json.dumps(body)


async def test_tools_list_changed_capability():
    s = await started()
    async with Client(s.server, mode="legacy") as c:
        assert c.server_capabilities.tools.list_changed is True
