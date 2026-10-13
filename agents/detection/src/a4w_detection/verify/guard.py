"""받은 정의 검사 (Phase 2 설계 11.4절, FR-334). 타겟을 부르는 쪽이 마지막으로 막는다.

- 형식: tool.v1 스키마, 상위 tool 참조 무결성
- 금지 scope: VERIFY_DENY_SCOPES (Workfluence는 admin)
- 경로: Detection이 보관한 현재 명세에 있는 (METHOD, path)만
- 쓰기 위치: 쓰기 tool 시나리오에서 대상을 가리키는 입력(경로 칸, 이름이 id·space인 칸)은
  시험 환경 값(`${sandbox.*}`)이나 그 안에서 만든 값(`${saved.*}`, `${setup.*}`)이어야 한다
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from typing import Any

from a4w_tools.request import READ_METHODS
from a4w_tools.validate import check_references, validate_tool

from .scenarios import PLACEHOLDER, SOURCES, placeholders

SAFE_SOURCES = frozenset({"sandbox", "saved", "setup"})
# 대상 자료를 가리키는 입력 이름 (경로 칸이 아니어도 다른 곳을 쓰게 할 수 있는 칸)
TARGET_NAME = re.compile(r"(^space$|^parent$|Id$|_id$|^id$|Ids$)")


def check_request(
    *,
    tools: list[Any],
    references: list[Any],
    scenarios: Mapping[str, Any],
    spec_operations: Collection[tuple[str, str]],
    deny_scopes: Collection[str],
    max_tools: int,
    context_keys: Collection[str],
) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []

    def add(tool: str, reason: str, message: str) -> None:
        problems.append({"tool": tool, "reason": reason, "message": message})

    if len(tools) > max_tools:
        add("*", "TOO_MANY_TOOLS", f"tool은 {max_tools}개까지 받는다 ({len(tools)}개)")

    defs: dict[str, dict[str, Any]] = {}
    verified: set[str] = set()
    for i, d in enumerate(list(tools) + list(references)):
        name = d.get("name", f"#{i}") if isinstance(d, Mapping) else f"#{i}"
        errors = validate_tool(d)
        if errors:
            add(name, "SCHEMA_INVALID", errors[0])
            continue
        defs[name] = d
        if i < len(tools):
            verified.add(name)
        denied = sorted(set(d["scopes"]) & set(deny_scopes))
        if denied:
            add(name, "DENIED_SCOPE", f"검증하지 않는 scope: {', '.join(denied)}")
        if d["kind"] == "operation" and (d["http"]["method"], d["http"]["path"]) not in spec_operations:
            add(name, "UNKNOWN_OPERATION", f"현재 명세에 없는 동작: {d['http']['method']} {d['http']['path']}")

    for message in check_references(defs):
        add(message.split(".", 1)[0], "REFERENCE_INVALID", message)

    for name, scenario in sorted(scenarios.items()):
        if name not in verified:
            add(name, "SCENARIO_UNKNOWN_TOOL", "검증 대상에 없는 tool의 시나리오")
            continue
        for source, path in placeholders(scenario):
            if source not in SOURCES or (source == "sandbox" and path not in context_keys):
                add(name, "SCENARIO_UNKNOWN_KEY", f"쓸 수 없는 값: ${{{source}.{path}}}")
        calls = [(defs[name], scenario.get("call", {}).get("args", {}))]
        for item in scenario.get("setup", []):
            target = defs.get(item.get("tool"))
            if target is None:
                add(name, "SCENARIO_UNKNOWN_TOOL", f"setup의 tool이 요청에 없다: {item.get('tool')}")
                continue
            calls.append((target, item.get("args", {})))
        for tool, args in calls:
            if not _is_write(tool, defs):
                continue
            for input_name in sorted(_target_inputs(tool, defs)):
                if input_name in args and not _safe(args[input_name]):
                    add(name, "WRITE_OUTSIDE_SANDBOX",
                        f"{tool['name']}.{input_name}는 시험 환경 값(${{sandbox.*}}, ${{saved.*}}, ${{setup.*}})이어야 한다")
    return problems


def _safe(value: Any) -> bool:
    m = PLACEHOLDER.fullmatch(value) if isinstance(value, str) else None
    return bool(m) and m.group(1) in SAFE_SOURCES


def _is_write(tool: Mapping[str, Any], defs: Mapping[str, Mapping[str, Any]]) -> bool:
    if tool["kind"] == "operation":
        return tool["http"]["method"] not in READ_METHODS
    if tool["kind"] == "composite":
        return any(s["tool"] in defs and _is_write(defs[s["tool"]], defs) for s in tool["steps"])
    return tool["risk"] != "read"


def _target_inputs(tool: Mapping[str, Any], defs: Mapping[str, Mapping[str, Any]]) -> set[str]:
    """그 tool의 입력 중 대상 자료를 가리키는 것."""
    if tool["kind"] == "operation":
        out = {b["input"] for b in tool["http"]["bindings"] if b["in"] == "path"}
        out |= {n for n in tool["inputSchema"]["properties"] if TARGET_NAME.search(n)}
        return out
    out: set[str] = set()
    if tool["kind"] == "composite":
        for step in tool["steps"]:
            target = defs.get(step["tool"])
            if target is None:
                continue
            for arg in _target_inputs(target, defs):
                ref = step["args"].get(arg, {})
                if "$input" in ref:
                    out.add(ref["$input"])
    return out
