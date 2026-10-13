"""명세 조각 지문 (Phase 2 설계 7.2절)."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from a4w_core.openapi.canonical import canonical_json, strip_doc_keys

from .transform.base import InputField


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def fragment_sha(fragment: Mapping[str, Any]) -> str:
    """seed가 그대로 맞는지."""
    return _sha(fragment)


def structure_sha(fragment: Mapping[str, Any]) -> str:
    """설명만 바뀌었는지 (문서용 키를 지운 조각)."""
    return _sha(strip_doc_keys(fragment))


def input_sha(field: InputField) -> str:
    """입력 칸 하나의 구조 (설명 제외). 바뀌지 않은 칸의 설명을 코드가 옮길 때 쓴다."""
    return _sha({"name": field.name, "location": field.location, "wire": field.wire, "pointer": field.pointer,
                 "required": field.required, "schema": strip_doc_keys(field.schema)})
