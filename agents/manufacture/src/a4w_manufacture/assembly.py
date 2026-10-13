"""정의 조립: 골격(코드) + 글(seed·LLM) → tool 정의 (Phase 2 설계 6.1·9.2절).

HTTP 매핑과 입력 칸은 골격에서만 온다. 글 쪽은 이름·설명·숨김·기본값·응답 필드·색인 문구만 정한다.
숨긴 칸은 inputSchema에서 빠지고 기본값과 함께 http.fixed로 간다.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from a4w_core.openapi.canonical import canonical_json

from .transform.base import Skeleton

SCHEMA_ID = "a4w.tool/v1"


class _NoDefault:
    def __repr__(self) -> str:
        return "NO_DEFAULT"


NO_DEFAULT: Any = _NoDefault()


@dataclass(frozen=True)
class InputAuthoring:
    description: str
    hide: bool | None = None   # None = 기계 기본(명세 기본값이 있는 선택 칸이면 숨김, 설계 6.3.1)
    default: Any = NO_DEFAULT


@dataclass(frozen=True)
class Authoring:
    name: str
    title: str
    description: str
    inputs: Mapping[str, InputAuthoring]
    summary: str
    examples: tuple[str, ...]
    max_chars: int
    response_fields: tuple[str, ...] | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


def parse_authoring(raw: Mapping[str, Any], *, max_chars: int) -> Authoring:
    """LLM 출력(또는 같은 모양)을 Authoring으로. 모양 검사는 checks.DefinitionChecker가 먼저 한다."""
    inputs = {
        name: InputAuthoring(description=v.get("description", ""), hide=v.get("hide"),
                             default=v["default"] if "default" in v else NO_DEFAULT)
        for name, v in raw["inputs"].items()
    }
    fields = raw.get("responseFields")
    return Authoring(
        name=raw["name"], title=raw["title"], description=raw["description"], inputs=inputs,
        summary=raw["index"]["summary"], examples=tuple(raw["index"]["examples"]), max_chars=max_chars,
        response_fields=tuple(fields) if fields else None,
    )


def authoring_to_raw(a: Authoring) -> dict[str, Any]:
    inputs = {}
    for name, ia in a.inputs.items():
        item: dict[str, Any] = {"description": ia.description}
        if ia.hide is not None:
            item["hide"] = ia.hide
        if ia.default is not NO_DEFAULT:
            item["default"] = ia.default
        inputs[name] = item
    raw: dict[str, Any] = {"name": a.name, "title": a.title, "description": a.description, "inputs": inputs,
                           "index": {"summary": a.summary, "examples": list(a.examples)}}
    if a.response_fields:
        raw["responseFields"] = list(a.response_fields)
    return raw


def assemble(
    skeleton: Skeleton, a: Authoring, *, group: str, risk: str, origin: str, source: Mapping[str, Any]
) -> dict[str, Any]:
    props: dict[str, Any] = {}
    required: list[str] = []
    bindings: list[dict[str, Any]] = []
    fixed = [dict(f) for f in skeleton.http.get("fixed", [])]
    omitted: list[str] = []
    for inp in skeleton.inputs:
        ia = a.inputs.get(inp.name, InputAuthoring(description=inp.description))
        hide = effective_hide(inp, ia)
        if hide and ia.default is not NO_DEFAULT:
            fixed.append(inp.fixed(ia.default))
            continue
        if hide and not inp.required and inp.location != "path":
            omitted.append(inp.name)  # 보내지 않는다 (서버 기본값)
            continue
        schema = dict(inp.schema)
        text = ia.description or inp.description
        if text:
            schema["description"] = text
        if ia.default is not NO_DEFAULT:
            schema["default"] = ia.default
        props[inp.name] = schema
        if inp.required and ia.default is NO_DEFAULT:
            required.append(inp.name)
        bindings.append(inp.binding())

    http: dict[str, Any] = {"method": skeleton.http["method"], "path": skeleton.http["path"]}
    if "contentType" in skeleton.http:
        http["contentType"] = skeleton.http["contentType"]
    http.update(bindings=bindings, fixed=fixed)
    if omitted:
        http["omitted"] = omitted
    http["success"] = list(skeleton.http["success"])
    response: dict[str, Any] = {"maxChars": a.max_chars}
    if a.response_fields:
        response = {"fields": list(a.response_fields), "maxChars": a.max_chars}

    definition: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "kind": "operation",
        "name": a.name,
        "title": a.title,
        "description": a.description,
        "inputSchema": {"type": "object", "properties": props, "required": required, "additionalProperties": False},
        "http": http,
        "response": response,
        "risk": risk,
        "scopes": list(skeleton.scopes),
        "group": group,
        "index": {"summary": a.summary, "examples": list(a.examples)},
        "source": dict(source),
        "origin": origin,
    }
    definition["revision"] = revision_of(definition)
    return definition


def extract_authoring(definition: Mapping[str, Any], skeleton: Skeleton) -> Authoring:
    """정의(seed·이전 버전)에서 글 쪽만 꺼낸다. 골격에 없는 칸은 버린다(검사가 따로 잡는다)."""
    props = definition.get("inputSchema", {}).get("properties", {})
    fixed = definition.get("http", {}).get("fixed", [])
    omitted = set(definition.get("http", {}).get("omitted", []))
    inputs: dict[str, InputAuthoring] = {}
    for inp in skeleton.inputs:
        if inp.name in props:
            p = props[inp.name]
            spec_default = inp.schema.get("default", NO_DEFAULT)
            default = p.get("default", NO_DEFAULT)
            if default is not NO_DEFAULT and spec_default is not NO_DEFAULT and default == spec_default:
                default = NO_DEFAULT
            # 보이는 칸은 "일부러 드러냄"으로 적는다 (기계 기본 숨김을 이긴다)
            inputs[inp.name] = InputAuthoring(description=p.get("description", ""),
                                              hide=False if inp.machine_hidden else None, default=default)
            continue
        if inp.name in omitted:
            inputs[inp.name] = InputAuthoring(description=inp.description, hide=True)
            continue
        match = next((f for f in fixed if _same_place(f, inp) and "value" in f), None)
        if match is not None:
            inputs[inp.name] = InputAuthoring(description=inp.description, hide=True, default=match["value"])
    response = definition.get("response", {})
    index = definition.get("index", {})
    return Authoring(
        name=definition.get("name", ""), title=definition.get("title", ""), description=definition.get("description", ""),
        inputs=inputs, summary=index.get("summary", ""), examples=tuple(index.get("examples", [])),
        max_chars=response.get("maxChars", 0), response_fields=tuple(response["fields"]) if response.get("fields") else None,
    )


def missing_inputs(definition: Mapping[str, Any], skeleton: Skeleton) -> list[str]:
    a = extract_authoring(definition, skeleton)
    return [i.name for i in skeleton.inputs if i.name not in a.inputs]


def invented_inputs(definition: Mapping[str, Any], skeleton: Skeleton) -> list[str]:
    known = {i.name for i in skeleton.inputs}
    return sorted(n for n in definition.get("inputSchema", {}).get("properties", {}) if n not in known)


def effective_hide(inp, ia: InputAuthoring) -> bool:
    """글 쪽이 정하지 않았으면(None) 기계 기본 숨김을 따른다 (설계 6.3.1)."""
    return inp.machine_hidden if ia.hide is None else ia.hide


def visible_inputs(skeleton: Skeleton, a: Authoring) -> list[str]:
    out = []
    for inp in skeleton.inputs:
        ia = a.inputs.get(inp.name, InputAuthoring(description=inp.description))
        hidden = effective_hide(inp, ia)
        if hidden and (ia.default is not NO_DEFAULT or (not inp.required and inp.location != "path")):
            continue
        out.append(inp.name)
    return out


def revision_of(definition: Mapping[str, Any]) -> str:
    body = {k: v for k, v in definition.items() if k != "revision"}
    return "r-" + hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()[:12]


def _same_place(fixed: Mapping[str, Any], inp) -> bool:
    if fixed.get("in") != inp.location:
        return False
    if inp.location == "body":
        return fixed.get("pointer") == inp.pointer
    return fixed.get("name") == inp.wire
