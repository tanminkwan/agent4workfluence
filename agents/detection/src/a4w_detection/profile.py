"""타겟 정보 (Phase 2 설계 11.5절, FR-331): token scope와 검증 환경 사용 가능 여부. token 값은 내주지 않는다."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from a4w_core.errors import TargetNotFound

from .verify.service import VerifyRuntime


class TargetProfileService:
    def __init__(self, *, known_targets: Collection[str], runtimes: Mapping[str, VerifyRuntime]):
        self._known = set(known_targets)
        self._runtimes = runtimes

    def profile(self, target_id: str) -> dict[str, Any]:
        if target_id not in self._known:
            raise TargetNotFound(f"등록되지 않은 타겟: {target_id}")
        rt = self._runtimes.get(target_id)
        if rt is None:
            return {"targetId": target_id, "tokenScopes": [],
                    "verification": {"available": False, "sandbox": None, "contextKeys": []}}
        scopes = rt.scope_provider.scopes(rt.token)  # 모르면 ScopeUnknown
        return {
            "targetId": target_id,
            "tokenScopes": sorted(scopes),
            "verification": {"available": True, "sandbox": rt.sandbox_name, "contextKeys": list(rt.context_keys)},
        }
