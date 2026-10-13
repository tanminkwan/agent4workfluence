"""시나리오가 없는 읽기 tool의 자동 시나리오 (Phase 2 설계 11.7절).

필수 칸을 (1) 같은 이름의 시험 환경 값, (2) enum의 첫 값·기본값, (3) 문자열이면 "a4w"로 채운다.
경로 칸은 시험 환경 값으로만 채운다(지어낸 id로 부르면 실패가 tool 탓처럼 보이므로). 쓰기 tool은 만들지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from a4w_tools.request import READ_METHODS

FILLER_TEXT = "a4w"


def auto_scenario(tool: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any] | None:
    if tool.get("kind") != "operation" or tool["http"]["method"] not in READ_METHODS:
        return None
    path_inputs = {b["input"] for b in tool["http"]["bindings"] if b["in"] == "path"}
    props = tool["inputSchema"].get("properties", {})
    args: dict[str, Any] = {}
    for name in tool["inputSchema"].get("required", []):
        schema = props.get(name, {})
        if name in context:
            args[name] = context[name]
        elif name in path_inputs:
            return None
        elif "default" in schema:
            args[name] = schema["default"]
        elif schema.get("enum"):
            args[name] = schema["enum"][0]
        elif _is_string(schema):
            args[name] = FILLER_TEXT
        else:
            return None
    return {"call": {"args": args}, "expect": {"ok": True}}


def _is_string(schema: Mapping[str, Any]) -> bool:
    t = schema.get("type")
    return t == "string" or (isinstance(t, list) and "string" in t)
