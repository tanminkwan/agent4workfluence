"""OpenAPI 3.x 조각 → tool 골격 (Phase 2 설계 7.1절).

- 입력 칸 순서: path → query → header → body, 같은 위치 안은 명세 순서
- 본문이 객체면 최상위 속성을 칸으로 펼친다. 경로·질의 칸과 이름이 겹치면 본문 칸에 `body_`
- 요청 형식은 설정 우선순위로, 응답은 JSON
- 순환 참조 표시(x-a4w-cycle)는 {"type": "object"}
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any

from .base import InputField, Skeleton, SpecTransformer

LOCATIONS = ("path", "query", "header")
SKIP_HEADERS = frozenset({"authorization"})   # 인증은 실행기가 넣는다
DROP_KEYS = frozenset({"example", "examples", "x-a4w-cycle"})
JSON = "application/json"
MAX_RESPONSE_DEPTH = 4
_NONE = object()


class OpenApi3Transformer(SpecTransformer):
    def __init__(self, *, content_types: Iterable[str]):
        self._content_types = list(content_types)

    def skeleton(self, fragment: Mapping[str, Any], *, fixed: Mapping[str, Mapping[str, Any]]) -> Skeleton:
        params = fragment.get("parameters") or []
        inputs: list[InputField] = []
        for location in LOCATIONS:
            for p in params:
                if p.get("in") != location:
                    continue
                if location == "header" and p["name"].lower() in SKIP_HEADERS:
                    continue
                schema = _clean(p.get("schema") or {})
                text = p.get("description") or schema.get("description") or ""
                if text:
                    schema["description"] = text
                inputs.append(InputField(
                    name=p["name"], location=location, wire=p["name"], pointer=None, schema=schema,
                    required=bool(p.get("required")) or location == "path", description=text,
                ))

        content_type, body_inputs = self._body(fragment.get("requestBody"), taken={i.name for i in inputs})
        inputs.extend(body_inputs)

        fixed_entries: list[dict[str, Any]] = []
        kept: list[InputField] = []
        for inp in inputs:
            key = inp.wire if inp.location != "body" else inp.pointer.lstrip("/")
            value = fixed.get(inp.location, {}).get(key, _NONE)
            if isinstance(value, dict) and value.get("$overridable"):     # 입력으로 남고 비우면 맥락 값 (설계 26절)
                kept.append(replace(inp, required=False, context_default=value["$context"]))
            elif value is not _NONE:
                fixed_entries.append(inp.fixed(value))
            else:
                kept.append(inp)

        http: dict[str, Any] = {"method": fragment["method"], "path": fragment["path"]}
        if content_type:
            http["contentType"] = content_type
        http["bindings"] = [i.binding() for i in kept]
        http["fixed"] = fixed_entries
        http["success"] = _success(fragment.get("responses") or {})

        return Skeleton(
            operation_id=fragment.get("operationId"),
            method=fragment["method"],
            path=fragment["path"],
            inputs=tuple(kept),
            http=http,
            response_paths=tuple(_response_paths(fragment.get("responses") or {}, http["success"])),
            scopes=_scopes(fragment),
            summary=fragment.get("summary") or "",
            description=fragment.get("description") or "",
            tags=tuple(fragment.get("tags") or ()),
        )

    def _body(self, body: Mapping[str, Any] | None, taken: set[str]) -> tuple[str | None, list[InputField]]:
        if not body:
            return None, []
        content = body.get("content") or {}
        chosen = next((ct for ct in self._content_types if ct in content), next(iter(content), None))
        if chosen is None:
            return None, []
        schema = _clean((content[chosen] or {}).get("schema") or {})
        body_required = bool(body.get("required"))
        props = schema.get("properties")
        if isinstance(props, dict) and props:
            required = set(schema.get("required") or []) if body_required else set()
            out = []
            for name, sub in props.items():
                field_name = name
                while field_name in taken:
                    field_name = "body_" + field_name
                taken.add(field_name)
                text = sub.get("description") or ""
                out.append(InputField(
                    name=field_name, location="body", wire=None, pointer="/" + name.replace("~", "~0").replace("/", "~1"),
                    schema=sub, required=name in required, description=text,
                ))
            return chosen, out
        name = "body"
        while name in taken:
            name = "body_" + name
        return chosen, [InputField(name=name, location="body", wire=None, pointer="", schema=schema,
                                   required=body_required, description=schema.get("description") or "")]


def _clean(schema: Any) -> Any:
    """설명 외의 예시를 빼고, 순환 표시는 객체로 바꾼다. 원본을 바꾸지 않는다."""
    if isinstance(schema, dict):
        if schema.get("x-a4w-cycle"):
            return {"type": "object"}
        return {k: _clean(v) for k, v in schema.items() if k not in DROP_KEYS and k != "$ref"}
    if isinstance(schema, list):
        return [_clean(v) for v in schema]
    return copy.deepcopy(schema)


def _success(responses: Mapping[str, Any]) -> list[int]:
    codes = sorted(int(k) for k in responses if k.isdigit() and 200 <= int(k) < 300)
    return codes or [200]


def _scopes(fragment: Mapping[str, Any]) -> tuple[str, ...]:
    security = fragment.get("security") or []
    if not security:
        return ()
    return tuple(sorted({s for values in security[0].values() for s in values}))


def _response_paths(responses: Mapping[str, Any], success: list[int]) -> list[str]:
    for code in success:
        resp = responses.get(str(code)) or {}
        schema = ((resp.get("content") or {}).get(JSON) or {}).get("schema")
        if schema:
            out: list[str] = []
            _walk(schema, "", out, 0)
            return sorted(set(out))
    return []


def _walk(schema: Any, prefix: str, out: list[str], depth: int) -> None:
    if not isinstance(schema, dict) or depth > MAX_RESPONSE_DEPTH:
        return
    for key in ("anyOf", "oneOf", "allOf"):
        for sub in schema.get(key) or []:
            _walk(sub, prefix, out, depth)
    for name, sub in (schema.get("properties") or {}).items():
        path = f"{prefix}.{name}" if prefix else name
        out.append(path)
        _walk(sub, path, out, depth + 1)
    if "items" in schema:
        path = prefix + "[]"
        out.append(path)
        _walk(schema["items"], path, out, depth + 1)
