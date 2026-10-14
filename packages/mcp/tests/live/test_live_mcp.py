"""시험 스택 실호출 (Phase 3 설계 10.3절, AC-T9). A4W_LIVE_TESTS=1 일 때만 돈다.

같은 container 안에서 `python -m a4w_mcp`를 stdio 자식 프로세스로 띄우고 SDK client로 붙는다.
정의는 compose의 manufacture(MCP_LIVE_MANUFACTURE_URL)에서, token은 agent 계정(config/secrets/agents)으로.
남기는 것: homeSpace의 바탕 페이지 하나(MCP_LIVE_PAGE_TITLE, 사용자 결정 2026-10-14). 댓글은 매번 지운다.
"""

from __future__ import annotations

import json
import os
import sys
import uuid

import anyio
import pytest
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

pytestmark = [pytest.mark.live, pytest.mark.anyio,
              pytest.mark.skipif(os.environ.get("A4W_LIVE_TESTS") != "1", reason="A4W_LIVE_TESTS=1 일 때만")]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def name(tool: str) -> str:
    return os.environ["MCP_TARGETS"].split(",")[0] + os.environ["MCP_TOOL_NAME_SEPARATOR"] + tool


def body(result):
    return json.loads(result.content[0].text)


@pytest.fixture
def params():
    env = dict(os.environ)
    env["MCP_MANUFACTURE_URL"] = os.environ["MCP_LIVE_MANUFACTURE_URL"]
    return StdioServerParameters(command=sys.executable, args=["-m", "a4w_mcp"], env=env)


async def ready(c: Client) -> dict:
    with anyio.fail_after(30):
        while True:
            status = json.loads((await c.read_resource("a4w://status")).contents[0].text)
            target = next(iter(status["targets"].values()))
            if target["toolVersion"]:
                return status
            await anyio.sleep(0.2)


async def base_page(c: Client, title: str) -> str:
    r = await c.call_tool(name("search_pages"), {"q": title})
    assert r.is_error is False, body(r)
    hits = [i for i in body(r)["data"]["items"] if i["title"] == title]
    if hits:
        return hits[0]["pageId"]
    made = await c.call_tool(name("create_page"), {"title": title, "body": "a4w MCP live 시험용 바탕 페이지. 지우지 마세요."})
    assert made.is_error is False, body(made)
    return body(made)["data"]["id"]


async def test_live_list_read_composite_write_and_revision(params):
    title = os.environ["MCP_LIVE_PAGE_TITLE"]
    async with Client(params, mode="2026-07-28") as c:
        status = await ready(c)
        target = next(iter(status["targets"].values()))
        assert target["lastError"] is None and target["contextMissing"] == []

        # 1. 목록: 서빙 묶음의 tool이 이름공간 이름으로, 맥락 칸 없이
        tools = {t.name: t for t in (await c.list_tools()).tools}
        assert len(tools) == target["served"] > 0
        assert all(n.startswith(name("")) for n in tools)
        assert "space" not in tools[name("search_pages")].input_schema["properties"]

        # 2·3. 읽기 + 상위 tool (homeSpace 안)
        page_id = await base_page(c, title)
        r = await c.call_tool(name("read_page_by_title"), {"title": title})
        assert r.is_error is False, body(r)
        assert body(r)["data"]["id"] == page_id

        # 4. 쓰기: 댓글 달기 → 지우기
        marker = f"a4w live {uuid.uuid4().hex[:8]}"
        made = await c.call_tool(name("add_comment"), {"pageId": page_id, "body": marker})
        assert made.is_error is False, body(made)
        comment_id = body(made)["data"]["id"]
        gone = await c.call_tool(name("delete_comment"), {"id": comment_id})
        assert gone.is_error is False, body(gone)

        # 5. 버전 일치: 틀린 revision이면 실행하지 않는다
        r = await c.call_tool(name("search_pages"), {"q": title}, meta={"a4w/expectedRevision": "r-000000000000"})
        assert r.is_error is True and body(r)["error"]["code"] == "REVISION_MISMATCH"
        current = tools[name("search_pages")].meta["a4w/revision"]
        r = await c.call_tool(name("search_pages"), {"q": title}, meta={"a4w/expectedRevision": current})
        assert r.is_error is False


async def test_live_legacy_protocol_lists_same_tools(params):
    async with Client(params, mode="legacy") as c:
        status = await ready(c)
        tools = (await c.list_tools()).tools
        assert len(tools) == next(iter(status["targets"].values()))["served"]
