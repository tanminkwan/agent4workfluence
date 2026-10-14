"""받은 묶음 검사와 서빙 Snapshot (Phase 3 설계 6.2·6.3절).

Snapshot은 불변이다. 갱신은 새 Snapshot을 만들어 참조만 바꾼다. 호출은 시작할 때 잡은 Snapshot으로 끝난다.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from a4w_tools.validate import check_references, validate_tool, validate_toolset

RISK_ORDER = {"read": 0, "write": 1, "danger": 2}
MCP_NAME = re.compile(r"^[A-Za-z0-9_-]+$")

HIDDEN_REASONS = ("SCOPE", "RISK", "GROUP", "NAME")

INVALID = "TOOLSET_INVALID"
MISMATCH = "TOOLSET_MISMATCH"


class ToolsetRejected(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Exposure:
    """한 타겟에서 무엇을 내보낼지 (agent 설정 + token scope + 이름 규칙)."""

    scopes: frozenset[str] | None   # None이면 scope로 거르지 않는다 (MCP_SCOPE_UNKNOWN=all)
    max_risk: str
    groups: frozenset[str] | None   # None이면 모든 묶음
    separator: str
    name_max: int
    context: Mapping[str, str]


@dataclass(frozen=True)
class ServedTool:
    mcp_name: str
    target_id: str
    definition: Mapping[str, Any]
    tool_version: str


@dataclass(frozen=True)
class Snapshot:
    tool_versions: Mapping[str, str] = field(default_factory=dict)            # targetId → toolVersion
    served: Mapping[str, ServedTool] = field(default_factory=dict)            # MCP 이름 → 노출 tool
    toolsets: Mapping[str, Mapping[str, Mapping[str, Any]]] = field(default_factory=dict)  # 숨긴 것 포함
    hidden: Mapping[str, str] = field(default_factory=dict)                   # MCP 이름 → 이유
    hidden_by_target: Mapping[str, Mapping[str, str]] = field(default_factory=dict)  # targetId → MCP 이름 → 이유
    context_missing: Mapping[str, list[str]] = field(default_factory=dict)    # targetId → 없는 맥락 키


def validate_bundle(target_id: str, tool_version: str, bundle: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """조회 API 묶음(5.2)을 검사하고 이름 → 정의를 돌려준다. 틀리면 ToolsetRejected."""
    manifest = bundle.get("manifest")
    tools = bundle.get("tools")
    if not isinstance(manifest, Mapping) or not isinstance(tools, Mapping):
        raise ToolsetRejected(INVALID, "묶음에 manifest·tools가 없다")
    problems = validate_toolset(manifest)
    if problems:
        raise ToolsetRejected(INVALID, f"manifest 형식: {problems[0]}")
    if not (bundle.get("targetId") == manifest["targetId"] == target_id
            and bundle.get("toolVersion") == manifest["toolVersion"] == tool_version):
        raise ToolsetRejected(MISMATCH, f"요청한 것과 다른 묶음: {target_id}/{tool_version}")
    listed = {e["name"]: e["revision"] for e in manifest["tools"]}
    if set(listed) != set(tools):
        raise ToolsetRejected(MISMATCH, "manifest의 tool 목록과 정의가 다르다")
    out: dict[str, dict[str, Any]] = {}
    for name, definition in tools.items():
        problems = validate_tool(definition)
        if problems:
            raise ToolsetRejected(INVALID, f"tool 형식 {name}: {problems[0]}")
        if definition["name"] != name or definition["revision"] != listed[name]:
            raise ToolsetRejected(MISMATCH, f"tool 이름·revision이 manifest와 다르다: {name}")
        out[name] = copy.deepcopy(dict(definition))
    problems = check_references(out)
    if problems:
        raise ToolsetRejected(INVALID, f"상위 tool 참조: {problems[0]}")
    return out


def build_snapshot(
    validated: Mapping[str, tuple[str, Mapping[str, Mapping[str, Any]]]], exposures: Mapping[str, Exposure]
) -> Snapshot:
    """validated: targetId → (toolVersion, 이름 → 정의)."""
    served: dict[str, ServedTool] = {}
    hidden: dict[str, str] = {}
    by_target: dict[str, dict[str, str]] = {}
    toolsets: dict[str, Mapping[str, Mapping[str, Any]]] = {}
    missing: dict[str, list[str]] = {}
    for target_id, (version, tools) in sorted(validated.items()):
        rule = exposures[target_id]
        frozen = {name: MappingProxyType(dict(t)) for name, t in tools.items()}
        toolsets[target_id] = MappingProxyType(frozen)
        needed: set[str] = set()
        for name, definition in sorted(frozen.items()):
            mcp_name = f"{target_id}{rule.separator}{name}"
            reason = _hidden_reason(definition, mcp_name, rule)
            if reason:
                hidden[mcp_name] = reason
                by_target.setdefault(target_id, {})[mcp_name] = reason
                continue
            served[mcp_name] = ServedTool(mcp_name, target_id, definition, version)
            needed |= _context_keys(definition, frozen)
        absent = sorted(needed - set(rule.context))
        if absent:
            missing[target_id] = absent
    return Snapshot(
        tool_versions=MappingProxyType({t: v for t, (v, _) in validated.items()}),
        served=MappingProxyType(served), toolsets=MappingProxyType(toolsets),
        hidden=MappingProxyType(hidden),
        hidden_by_target=MappingProxyType({t: MappingProxyType(h) for t, h in by_target.items()}),
        context_missing=MappingProxyType(missing),
    )


def _hidden_reason(definition: Mapping[str, Any], mcp_name: str, rule: Exposure) -> str | None:
    if rule.scopes is not None and not set(definition.get("scopes", [])) <= rule.scopes:
        return "SCOPE"
    if RISK_ORDER[definition["risk"]] > RISK_ORDER[rule.max_risk]:
        return "RISK"
    if rule.groups is not None and definition.get("group") not in rule.groups:
        return "GROUP"
    if not MCP_NAME.match(mcp_name) or len(mcp_name) > rule.name_max:
        return "NAME"
    return None


def _context_keys(definition: Mapping[str, Any], toolset: Mapping[str, Mapping[str, Any]]) -> set[str]:
    if definition["kind"] == "composite":
        return set().union(*(_context_keys(toolset[s["tool"]], toolset) for s in definition["steps"]))
    return {f["context"] for f in definition.get("http", {}).get("fixed", []) if "context" in f}
