"""사람 확인 (Phase 2 설계 12.2절): 결정만 기록한다. 반영은 다음 POST /v1/apply 때."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .errors import ReviewAlreadyDecided, ReviewNotFound
from .storage.sqlite_db import SqliteManufactureDb

DECISIONS = {"approve": "approved", "reject": "rejected"}


class ReviewService:
    def __init__(self, db: SqliteManufactureDb, *, clock: Callable[[], str]):
        self._db = db
        self._clock = clock

    def list(self, target_id: str, status: str | None) -> list[dict[str, Any]]:
        return [_brief(r) for r in self._db.reviews(target_id, status=status)]

    def get(self, review_id: str) -> dict[str, Any]:
        r = self._db.review(review_id)
        if r is None:
            raise ReviewNotFound(f"보류 항목이 없다: {review_id}")
        return r

    def decide(self, review_id: str, decision: str, *, by: str | None, note: str | None) -> dict[str, Any]:
        self.get(review_id)
        if not self._db.decide_review(review_id, decision=DECISIONS[decision], by=by, note=note, at=self._clock()):
            raise ReviewAlreadyDecided(f"이미 결정한 보류 항목이다: {review_id}")
        return self.get(review_id)


def _brief(r: dict[str, Any]) -> dict[str, Any]:
    d = r.get("definition") or {}
    return {k: r[k] for k in ("reviewId", "status", "operationId", "toolName", "reasons", "applyId", "verify")} | {
        "definition": {"name": d.get("name"), "title": d.get("title"), "risk": d.get("risk")} if d else None}
