"""도메인 값 → 응답 JSON. 해당 없는 키는 넣지 않는다 (계약)."""

from __future__ import annotations

from typing import Any

from a4w_core.models import ChangeSet, ChangeSummary, OperationChange

from ..service import DetectResult


def summary(s: ChangeSummary) -> dict[str, int]:
    return {"added": s.added, "removed": s.removed, "modified": s.modified,
            "docOnly": s.doc_only, "documentChanges": s.document_changes}


def detect_result(r: DetectResult) -> dict[str, Any]:
    if r.status == "unchanged":
        return {"status": "unchanged", "targetId": r.target_id, "currentVersion": r.current_version}
    return {
        "status": "changed", "targetId": r.target_id, "changeSetId": r.change_set_id,
        "fromVersion": r.from_version, "toVersion": r.to_version, "summary": summary(r.summary),
        "needsReview": r.needs_review, "reviewReasons": list(r.review_reasons),
    }


def operation(o: OperationChange) -> dict[str, Any]:
    out: dict[str, Any] = {"method": o.method, "path": o.path, "operationId": o.operation_id, "change": o.change}
    if o.before is not None:
        out["before"] = o.before
    if o.after is not None:
        out["after"] = o.after
    if o.change == "modified":
        out["changedFields"] = list(o.changed_fields or ())
        out["docOnly"] = bool(o.doc_only)
    return out


def changeset(cs: ChangeSet, include_text_diff: bool) -> dict[str, Any]:
    out = {
        "changeSetId": cs.change_set_id, "targetId": cs.target_id,
        "fromVersion": cs.from_version, "toVersion": cs.to_version, "createdAt": cs.created_at,
        "needsReview": cs.review.needs_review, "reviewReasons": list(cs.review.reasons),
        "summary": summary(cs.summary),
        "operations": [operation(o) for o in cs.operations],
        "documentChanges": [{"section": d.section, "change": d.change} for d in cs.document_changes],
    }
    if include_text_diff:
        out["textDiff"] = cs.text_diff
    return out
