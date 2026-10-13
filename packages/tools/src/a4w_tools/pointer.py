"""JSON Pointer(RFC 6901) 읽기."""

from __future__ import annotations

from typing import Any


class PointerMissing(LookupError):
    pass


def resolve(doc: Any, pointer: str) -> Any:
    if pointer in ("", "/"):
        return doc
    node = doc
    for raw in pointer.lstrip("/").split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                raise PointerMissing(pointer) from None
        elif isinstance(node, dict) and part in node:
            node = node[part]
        else:
            raise PointerMissing(pointer)
    return node
