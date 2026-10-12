"""문서 안 `$ref`(#/...)를 풀어 넣는다. 순환은 두 번째 만남에서 끊고 표시한다."""

from __future__ import annotations

from typing import Any

from ..errors import SpecInvalid

CYCLE_MARK = "x-a4w-cycle"


def _pointer_get(doc: dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        raise SpecInvalid(f"외부 참조는 지원하지 않는다: {ref}")
    node: Any = doc
    for raw in ref[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and token in node:
            node = node[token]
        elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
            node = node[int(token)]
        else:
            raise SpecInvalid(f"참조 대상이 없다: {ref}")
    return node


def resolve_refs(node: Any, doc: dict[str, Any]) -> Any:
    """node의 복사본을 돌려준다. 입력은 바꾸지 않는다."""

    def walk(n: Any, stack: tuple[str, ...]) -> Any:
        if isinstance(n, dict):
            ref = n.get("$ref")
            if isinstance(ref, str):
                if ref in stack:
                    return {"$ref": ref, CYCLE_MARK: True}
                target = walk(_pointer_get(doc, ref), stack + (ref,))
                siblings = {k: walk(v, stack) for k, v in n.items() if k != "$ref"}
                if not siblings:
                    return target
                if isinstance(target, dict):
                    return {**target, **siblings}
                return target
            return {k: walk(v, stack) for k, v in n.items()}
        if isinstance(n, list):
            return [walk(v, stack) for v in n]
        return n

    return walk(node, ())
