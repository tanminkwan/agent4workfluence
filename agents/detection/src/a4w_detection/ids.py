"""식별자 규칙 (설계 7절). 형식 자체라 설정 대상이 아니다."""

from __future__ import annotations

import hashlib

CHANGE_SET_PREFIX = "cs-"
CHANGE_SET_HEX_LEN = 24


def spec_version(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def change_set_id(target_id: str, from_version: str | None, to_version: str) -> str:
    key = f"{target_id}\n{from_version or 'empty'}\n{to_version}".encode()
    return CHANGE_SET_PREFIX + hashlib.sha256(key).hexdigest()[:CHANGE_SET_HEX_LEN]
