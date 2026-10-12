"""OpenAPI 3.x 문서에서 동작 조각을 만든다."""

from __future__ import annotations

from typing import Any

from ..errors import SpecInvalid
from .resolver import resolve_refs

# OpenAPI 3.x 명세가 정한 동작 메서드 (설정 값이 아니라 표준이다)
HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")

FRAGMENT_KEYS = ("operationId", "summary", "description", "tags", "parameters", "requestBody", "responses")

OperationKey = tuple[str, str]


def _merge_parameters(path_params: list[Any], op_params: list[Any]) -> list[Any]:
    def key(p: Any) -> tuple[Any, Any]:
        return (p.get("name"), p.get("in")) if isinstance(p, dict) else (None, None)

    op_keys = {key(p) for p in op_params}
    return [p for p in path_params if key(p) not in op_keys] + list(op_params)


def collect_operations(doc: dict[str, Any]) -> dict[OperationKey, dict[str, Any]]:
    """`(METHOD, path)` → 풀어 넣은 동작 조각."""
    paths = doc.get("paths")
    if not isinstance(paths, dict):
        raise SpecInvalid("paths가 객체가 아니다")
    top_security = doc.get("security")
    result: dict[OperationKey, dict[str, Any]] = {}
    for path, item in paths.items():
        if not isinstance(item, dict):
            raise SpecInvalid(f"경로 항목이 객체가 아니다: {path}")
        item = resolve_refs(item, doc)
        path_params = item.get("parameters") or []
        for method in HTTP_METHODS:
            op = item.get(method)
            if op is None:
                continue
            if not isinstance(op, dict):
                raise SpecInvalid(f"동작이 객체가 아니다: {method} {path}")
            frag: dict[str, Any] = {"method": method.upper(), "path": path}
            for k in FRAGMENT_KEYS:
                if k in op:
                    frag[k] = op[k]
            params = _merge_parameters(path_params, op.get("parameters") or [])
            if params:
                frag["parameters"] = params
            security = op["security"] if "security" in op else top_security
            if security is not None:
                frag["security"] = security
            result[(method.upper(), path)] = frag
    return result
