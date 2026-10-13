"""tool 정의 + 입력 값 → HTTP 요청 (설계 6.1절, 15.5절)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from .errors import CONTEXT_MISSING, INPUT_INVALID, UNSUPPORTED, ToolError, ToolFailure

JSON = "application/json"
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_LEFT = re.compile(r"\{[^}]*\}")


@dataclass
class HttpRequest:
    method: str
    path: str
    query: dict[str, str | list[str]] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    json_body: Any = None

    @property
    def is_write(self) -> bool:
        return self.method not in READ_METHODS


def build_request(
    definition: Mapping[str, Any], args: Mapping[str, Any], *, context: Mapping[str, Any] | None = None
) -> HttpRequest:
    """context: agent의 고정 맥락 값 (Phase 2 설계 6.3.1). 정의의 fixed가 값 대신 맥락 키를 가질 수 있다."""
    http = definition["http"]
    content_type = http.get("contentType", JSON)
    req = HttpRequest(method=http["method"], path=http["path"])
    body: Any = _Unset
    if content_type != JSON:
        raise ToolFailure(ToolError(UNSUPPORTED, f"지원하지 않는 요청 형식: {content_type}"))

    def put(location: str, name: str | None, pointer: str | None, value: Any) -> None:
        nonlocal body
        if location == "path":
            req.path = req.path.replace("{" + name + "}", quote(_text(value), safe=""))
        elif location == "query":
            if value is not None:
                req.query[name] = [_text(v) for v in value] if isinstance(value, list) else _text(value)
        elif location == "header":
            if value is not None:
                req.headers[name] = _text(value)
        else:
            body = _set_pointer(body, pointer, value)

    for b in http.get("bindings", []):
        if b["input"] in args:
            put(b["in"], b.get("name"), b.get("pointer"), args[b["input"]])
    for f in http.get("fixed", []):
        if "context" in f:
            if not context or f["context"] not in context:
                raise ToolFailure(ToolError(CONTEXT_MISSING, f"실행 맥락에 값이 없다: {f['context']}",
                                            hint="실행하는 쪽 설정(AGENT_CONTEXT)을 확인하라"))
            put(f["in"], f.get("name"), f.get("pointer"), context[f["context"]])
        else:
            put(f["in"], f.get("name"), f.get("pointer"), f["value"])

    left = _LEFT.findall(req.path)
    if left:
        raise ToolFailure(ToolError(INPUT_INVALID, f"경로 값이 비었다: {', '.join(left)}"))
    req.json_body = None if body is _Unset else body
    return req


class _UnsetType:
    pass


_Unset = _UnsetType()


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _set_pointer(body: Any, pointer: str, value: Any) -> Any:
    if pointer in ("", "/"):
        return value
    parts = [p.replace("~1", "/").replace("~0", "~") for p in pointer.lstrip("/").split("/")]
    root = {} if body is _Unset or not isinstance(body, dict) else body
    node = root
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value
    return root
