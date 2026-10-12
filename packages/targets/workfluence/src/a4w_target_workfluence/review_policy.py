"""Workfluence 검토 정책: v1은 더하기만 한다. 그렇지 않은 변경을 사람에게 알린다 (설계 6절)."""

from __future__ import annotations

from collections.abc import Sequence

from a4w_core.interfaces import ReviewPolicy
from a4w_core.models import DocumentChange, OperationChange, ReviewResult

SECURITY_SECTIONS = frozenset({"security", "components.securitySchemes"})


class WorkfluenceV1AdditivePolicy(ReviewPolicy):
    def evaluate(
        self,
        operations: Sequence[OperationChange],
        document_changes: Sequence[DocumentChange],
    ) -> ReviewResult:
        reasons: set[str] = set()
        for o in operations:
            if o.change == "removed":
                reasons.add("OPERATION_REMOVED")
            elif o.change == "modified" and not o.doc_only:
                reasons.add("OPERATION_CHANGED")
        for d in document_changes:
            if d.change == "added":
                continue  # 첫 실행 등 새로 생긴 섹션은 더하기다
            if d.section in SECURITY_SECTIONS:
                reasons.add("SECURITY_CHANGED")
            elif d.section == "openapi":
                reasons.add("SPEC_VERSION_CHANGED")
        ordered = tuple(sorted(reasons))
        return ReviewResult(needs_review=bool(ordered), reasons=ordered)
