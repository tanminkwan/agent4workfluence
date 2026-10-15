"""MCP 풀 시험용 가짜 server (stdio). a4w_mcp와 같은 모양(_meta, 결과 구조, 변경 알림)으로 말한다.

상태는 FAKE_STATE_DIR의 파일로 시험이 조종한다:
  tools.json   tool 목록 (바뀌면 알림을 보낸다)      crash   있으면 프로세스가 죽는다
  env.json     시작할 때 받은 환경변수 이름          calls.jsonl  받은 tools/call 기록
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import anyio
import mcp.types as t
from mcp.server import Server
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError

from a4w_mcp.notifier import ChangeNotifier

STATE = Path(os.environ["FAKE_STATE_DIR"])
notifier = ChangeNotifier()


class FakeServer(Server):
    def create_initialization_options(self, notification_options=None, *a, **k):
        return super().create_initialization_options(notification_options or NotificationOptions(tools_changed=True), *a, **k)


def tools() -> list[dict]:
    return json.loads((STATE / "tools.json").read_text(encoding="utf-8"))


def to_tool(d: dict) -> t.Tool:
    meta = {"a4w/targetId": "workfluence", "a4w/name": d["name"], "a4w/revision": d["revision"], "a4w/risk": d.get("risk", "read"),
            "a4w/group": "read"}
    meta.update(d.get("meta", {}))
    return t.Tool(name=f"workfluence__{d['name']}", title=d["name"], description=d.get("description", d["name"]),
                  input_schema=d.get("schema", {"type": "object", "properties": {}}), meta=meta)


async def list_tools(ctx, params):
    notifier.remember(ctx)
    return t.ListToolsResult(tools=[to_tool(d) for d in tools()])


async def call_tool(ctx, params):
    notifier.remember(ctx)
    if (STATE / "crash").exists():
        os._exit(1)
    meta = params.meta if isinstance(params.meta, dict) else (params.meta.model_dump(by_alias=True) if params.meta else {})
    with (STATE / "calls.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"name": params.name, "arguments": params.arguments, "meta": meta}, ensure_ascii=False) + "\n")
    by_name = {f"workfluence__{d['name']}": d for d in tools()}
    tool = by_name.get(params.name)
    if tool is None:
        raise MCPError(t.INVALID_PARAMS, f"없는 tool: {params.name}")
    expected = meta.get("a4w/expectedRevision")
    if expected and expected != tool["revision"]:
        body = {"error": {"code": "REVISION_MISMATCH", "message": "revision이 다르다", "hint": "tool을 다시 검색하라",
                          "details": {"expected": expected, "current": tool["revision"]}}}
        return result(body, True, {})
    if tool.get("fail"):
        return result({"error": {"code": tool["fail"], "message": "실패", "hint": "고쳐서 다시", "requestId": "wf-req-err"}}, True, {})
    return result({"data": {"echo": params.arguments, "tool": tool["name"]}, "truncated": False}, False,
                  {"a4w/requestId": "wf-req-1", "a4w/attempts": 1})


def result(body: dict, is_error: bool, meta: dict) -> t.CallToolResult:
    text = json.dumps(body, ensure_ascii=False)
    return t.CallToolResult(content=[t.TextContent(type="text", text=text)], structured_content=json.loads(text), is_error=is_error, meta=meta)


async def watch() -> None:
    last = (STATE / "tools.json").stat().st_mtime_ns
    while True:
        await anyio.sleep(0.03)
        if (STATE / "crash").exists():
            os._exit(1)
        now = (STATE / "tools.json").stat().st_mtime_ns
        if now != last:
            last = now
            await notifier.tools_changed()


async def main() -> None:
    (STATE / "env.json").write_text(json.dumps(sorted(os.environ)), encoding="utf-8")
    server = FakeServer("fake-a4w-mcp", version="0", on_list_tools=list_tools, on_call_tool=call_tool,
                        on_subscriptions_listen=notifier.listen_handler)
    async with stdio_server() as (read, write), anyio.create_task_group() as tg:
        tg.start_soon(watch)
        await server.run(read, write, server.create_initialization_options())
        tg.cancel_scope.cancel()


if __name__ == "__main__":
    print("fake mcp start", file=sys.stderr)
    anyio.run(main)
