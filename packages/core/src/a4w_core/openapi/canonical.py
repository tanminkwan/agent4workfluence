"""비교용 정규 JSON과 문서용 키 제거."""

from __future__ import annotations

import json
from typing import Any

# 설명만 담는 OpenAPI·JSON Schema 키워드
DOC_KEYS = frozenset({"summary", "description", "example", "examples", "title"})
# 값이 "이름 → 스키마"인 객체. 이 안의 키는 키워드가 아니라 이름이다.
NAME_MAP_KEYS = frozenset({"properties", "patternProperties", "$defs", "definitions"})


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def strip_doc_keys(value: Any) -> Any:
    def walk(n: Any, names: bool) -> Any:
        if isinstance(n, dict):
            out = {}
            for k, v in n.items():
                if not names and k in DOC_KEYS:
                    continue
                out[k] = walk(v, names=(not names and k in NAME_MAP_KEYS))
            return out
        if isinstance(n, list):
            return [walk(v, False) for v in n]
        return n

    return walk(value, False)
