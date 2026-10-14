"""정의 ↔ MCP 모양 (Phase 3 설계 7.1·7.3절)."""

from __future__ import annotations

import json
from typing import Any

import mcp.types as t
from a4w_tools.errors import ToolResult

from .snapshot import ServedTool

META = "a4w/"  # _meta 키 앞머리 (식별 규칙이라 설정이 아니다)
EXPECTED_REVISION = META + "expectedRevision"
REVISION_MISMATCH = "REVISION_MISMATCH"

# risk → (read_only, destructive, idempotent). 타겟은 늘 바깥 시스템이라 open_world는 true
ANNOTATIONS = {"read": (True, False, True), "write": (False, False, False), "danger": (False, True, False)}


def to_mcp_tool(tool: ServedTool) -> t.Tool:
    d = tool.definition
    read_only, destructive, idempotent = ANNOTATIONS[d["risk"]]
    return t.Tool(
        name=tool.mcp_name, title=d["title"], description=d["description"],
        input_schema=json.loads(json.dumps(d["inputSchema"])),  # 읽기 전용 정의를 SDK에 넘기기 전에 복사
        annotations=t.ToolAnnotations(title=d["title"], read_only_hint=read_only, destructive_hint=destructive,
                                      idempotent_hint=idempotent, open_world_hint=True),
        meta={META + "targetId": tool.target_id, META + "name": d["name"], META + "revision": d["revision"],
              META + "risk": d["risk"], META + "toolVersion": tool.tool_version, META + "group": d.get("group")},
    )


def to_call_result(result: ToolResult, tool: ServedTool) -> t.CallToolResult:
    meta: dict[str, Any] = {META + "revision": tool.definition["revision"], META + "toolVersion": tool.tool_version}
    for key in ("requestId", "attempts", "elapsedMs"):
        if key in result.meta:
            meta[META + key] = result.meta[key]
    if result.ok:
        body: dict[str, Any] = {"data": result.data, "truncated": result.truncated}
    else:
        body = {"error": result.error.to_dict()}
    return _result(body, is_error=not result.ok, meta=meta)


def mismatch_result(tool: ServedTool, expected: str) -> t.CallToolResult:
    error = {"code": REVISION_MISMATCH, "message": "검색한 tool과 서빙 중인 tool의 정의가 다르다",
             "details": {"expected": expected, "current": tool.definition["revision"]},
             "hint": "tool을 다시 검색하라. 색인이 아직 바뀌지 않았을 수 있다"}
    return _result({"error": error}, is_error=True,
                   meta={META + "revision": tool.definition["revision"], META + "toolVersion": tool.tool_version})


def _result(body: dict[str, Any], *, is_error: bool, meta: dict[str, Any]) -> t.CallToolResult:
    text = json.dumps(body, ensure_ascii=False)
    return t.CallToolResult(content=[t.TextContent(type="text", text=text)], structured_content=json.loads(text),
                            is_error=is_error, meta=meta)
