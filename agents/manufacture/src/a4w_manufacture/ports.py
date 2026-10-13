"""manufacture가 기대는 바깥 interface (DIP). 구현은 조립 지점(wiring.py)이 끼운다.

타겟 쪽(변경분, scope, 실호출 검증)은 모두 Detection을 거친다. manufacture는 타겟을 모른다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from a4w_core.models import ChangeSet


class ChangeSetSource(Protocol):
    def changeset(self, target_id: str, change_set_id: str) -> ChangeSet: ...


class TargetProfileSource(Protocol):
    def scopes(self, target_id: str) -> frozenset[str]: ...


class VerificationPort(Protocol):
    def verify(self, target_id: str, verification_id: str, *, tools: list[Mapping[str, Any]],
               references: list[Mapping[str, Any]], scenarios: Mapping[str, Any]) -> dict[str, Any]:
        """{"results": {tool 이름: {"result", "calls", "reason"?, "error"?}}, "sandbox": {...}}"""


class DetectionPort(ChangeSetSource, TargetProfileSource, VerificationPort, Protocol):
    """Detection 하나가 세 역할을 다 한다 (타겟 창구)."""
