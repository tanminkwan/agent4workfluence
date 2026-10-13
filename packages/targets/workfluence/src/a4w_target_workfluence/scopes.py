"""Workfluence token scope: token(JWT) payload의 `scope` 클레임(공백 구분)을 읽는다.

Workfluence에는 token 정보 API(`/me` 등)가 없다. 권한을 주는 용도가 아니라 "권한이 없을 tool을
미리 빼는" 용도라서 서명은 검증하지 않는다. 실제로 모자라면 실행 때 403 INSUFFICIENT_SCOPE로 드러난다.
"""

from __future__ import annotations

import base64
import binascii
import json

from a4w_core.errors import ScopeUnknown
from a4w_core.interfaces import ScopeProvider

CLAIM = "scope"


class WorkfluenceJwtScopeProvider(ScopeProvider):
    def scopes(self, token: str | None) -> frozenset[str]:
        parts = (token or "").split(".")
        if len(parts) != 3:
            raise ScopeUnknown("token이 JWT 모양이 아니다")
        try:
            raw = parts[1] + "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(raw))
        except (binascii.Error, ValueError, UnicodeDecodeError):
            raise ScopeUnknown("token payload를 읽을 수 없다") from None
        claim = payload.get(CLAIM) if isinstance(payload, dict) else None
        if not isinstance(claim, str) or not claim.strip():
            raise ScopeUnknown(f"token에 {CLAIM} 클레임이 없다")
        return frozenset(claim.split())
