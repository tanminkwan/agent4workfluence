"""두 OpenAPI 문서를 동작 단위로 비교한다 (설계 5절)."""

from __future__ import annotations

from typing import Any

from a4w_core.models import DocumentChange, OperationChange
from a4w_core.openapi.canonical import canonical_json, strip_doc_keys
from a4w_core.openapi.operations import collect_operations

# 동작이 아닌 문서 수준 섹션 (보고 순서)
DOCUMENT_SECTIONS = ("openapi", "info", "servers", "tags", "security", "components.securitySchemes")

_MISSING = object()


def _section(doc: dict[str, Any] | None, name: str) -> Any:
    node: Any = doc or {}
    for part in name.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _classify(before: Any, after: Any) -> str | None:
    if before is _MISSING and after is _MISSING:
        return None
    if before is _MISSING:
        return "added"
    if after is _MISSING:
        return "removed"
    return "modified" if canonical_json(before) != canonical_json(after) else None


class OpenApiDiffer:
    def diff(
        self, old: dict[str, Any] | None, new: dict[str, Any]
    ) -> tuple[tuple[OperationChange, ...], tuple[DocumentChange, ...]]:
        return self._operations(old, new), self._documents(old, new)

    def _operations(self, old, new) -> tuple[OperationChange, ...]:
        before = collect_operations(old) if old is not None else {}
        after = collect_operations(new)
        changes: list[OperationChange] = []
        for method, path in sorted(set(before) | set(after), key=lambda k: (k[1], k[0])):
            b, a = before.get((method, path)), after.get((method, path))
            if b is None:
                changes.append(OperationChange(method, path, a.get("operationId"), "added", after=a))
            elif a is None:
                changes.append(OperationChange(method, path, b.get("operationId"), "removed", before=b))
            elif canonical_json(b) != canonical_json(a):
                fields = tuple(
                    sorted(k for k in set(b) | set(a) if canonical_json(b.get(k)) != canonical_json(a.get(k)))
                )
                doc_only = canonical_json(strip_doc_keys(b)) == canonical_json(strip_doc_keys(a))
                changes.append(
                    OperationChange(
                        method, path, a.get("operationId"), "modified",
                        before=b, after=a, changed_fields=fields, doc_only=doc_only,
                    )
                )
        return tuple(changes)

    def _documents(self, old, new) -> tuple[DocumentChange, ...]:
        out = []
        for name in DOCUMENT_SECTIONS:
            kind = _classify(_section(old, name), _section(new, name))
            if kind:
                out.append(DocumentChange(name, kind))
        return tuple(out)
