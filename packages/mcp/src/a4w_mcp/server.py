"""MCP server (Phase 3 설계 7절): 저수준 Server에 handler를 넣는다. tool이 동적이라 데코레이터 방식은 쓰지 않는다."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

import anyio
import anyio.to_thread
import mcp.types as t
from mcp.server import Server
from mcp.server.lowlevel.server import NotificationOptions
from mcp.shared.exceptions import MCPError

from .catalog import ServingCatalog
from .mapping import EXPECTED_REVISION, mismatch_result, to_call_result, to_mcp_tool
from .notifier import ChangeNotifier
from .refresher import Refresher
from .runner import ToolRunner
from .snapshot import ServedTool

log = logging.getLogger(__name__)
STATUS_URI = "a4w://status"


class _A4wServer(Server):
    """transport는 create_initialization_options()를 인자 없이 부른다. tools listChanged를 기본으로 알린다."""

    def create_initialization_options(self, notification_options=None, *args, **kwargs):
        return super().create_initialization_options(
            notification_options or NotificationOptions(tools_changed=True), *args, **kwargs)


def build_server(*, name: str, version: str, agent_id: str, catalog: ServingCatalog, runner: ToolRunner,
                 refresher: Refresher, notifier: ChangeNotifier, max_concurrent: int,
                 scopes: Mapping[str, list[str] | None]) -> Server:
    limit = anyio.Semaphore(max_concurrent)

    async def list_tools(ctx, params) -> t.ListToolsResult:
        notifier.remember(ctx)
        return t.ListToolsResult(tools=[to_mcp_tool(s) for s in catalog.snapshot().served.values()])

    async def call_tool(ctx, params: t.CallToolRequestParams) -> t.CallToolResult:
        notifier.remember(ctx)
        snap = catalog.snapshot()
        tool = _served(snap.served, params.name)
        expected = _meta(params.meta).get(EXPECTED_REVISION)
        if expected and expected != tool.definition["revision"]:
            await refresher.refresh_now()
            snap = catalog.snapshot()
            tool = _served(snap.served, params.name)
            if expected != tool.definition["revision"]:
                log.info("call tool=%s revision=%s ok=false code=REVISION_MISMATCH expected=%s",
                         tool.mcp_name, tool.definition["revision"], expected)
                return mismatch_result(tool, expected)
        toolset = snap.toolsets[tool.target_id]  # 시작할 때 잡은 Snapshot으로 끝낸다 (6.5)
        async with limit:
            result = await anyio.to_thread.run_sync(runner.run, tool, dict(params.arguments or {}), toolset)
        log.info("call tool=%s revision=%s ok=%s code=%s status=%s attempts=%s elapsedMs=%s requestId=%s",
                 tool.mcp_name, tool.definition["revision"], str(result.ok).lower(),
                 result.error.code if result.error else "-", result.meta.get("status", "-"),
                 result.meta.get("attempts", "-"), result.meta.get("elapsedMs", "-"),
                 (result.error.request_id if result.error else None) or result.meta.get("requestId", "-"))
        return to_call_result(result, tool)

    async def list_resources(ctx, params) -> t.ListResourcesResult:
        notifier.remember(ctx)
        return t.ListResourcesResult(resources=[t.Resource(uri=STATUS_URI, name="status", title="MCP server 상태",
                                                           mime_type="application/json")])

    async def read_resource(ctx, params: t.ReadResourceRequestParams) -> t.ReadResourceResult:
        notifier.remember(ctx)
        if str(params.uri) != STATUS_URI:
            raise MCPError(t.INVALID_PARAMS, f"없는 resource: {params.uri}")
        body = {"agentId": agent_id, **catalog.status(), "scopes": dict(scopes)}
        return t.ReadResourceResult(contents=[t.TextResourceContents(
            uri=STATUS_URI, mime_type="application/json", text=json.dumps(body, ensure_ascii=False))])

    return _A4wServer(name, version=version, on_list_tools=list_tools, on_call_tool=call_tool,
                      on_list_resources=list_resources, on_read_resource=read_resource,
                      on_subscriptions_listen=notifier.listen_handler)


def _served(served: Mapping[str, ServedTool], name: str) -> ServedTool:
    tool = served.get(name)
    if tool is None:
        # 없는 tool과 숨긴 tool을 구별해 알리지 않는다 (이유는 상태·로그에)
        raise MCPError(t.INVALID_PARAMS, f"없는 tool: {name}")
    return tool


def _meta(meta: Any) -> dict[str, Any]:
    if meta is None:
        return {}
    if isinstance(meta, Mapping):
        return dict(meta)
    return meta.model_dump(by_alias=True, exclude_none=True)
