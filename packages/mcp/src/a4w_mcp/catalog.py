"""서빙 중인 Snapshot과 갱신 상태 (Phase 3 설계 6.5·7.5절).

replace()는 참조만 바꾼다. event loop 한 thread에서만 부르므로 원자적이다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any

from .snapshot import HIDDEN_REASONS, Snapshot


class ServingCatalog:
    def __init__(self, *, targets: Iterable[str], clock: Callable[[], str]):
        self._snapshot = Snapshot()
        self._clock = clock
        self._state: dict[str, dict[str, Any]] = {
            t: {"lastFetchedAt": None, "lastCheckedAt": None, "lastError": None} for t in targets
        }

    def snapshot(self) -> Snapshot:
        return self._snapshot

    def replace(self, snapshot: Snapshot, fetched: Iterable[str]) -> None:
        self._snapshot = snapshot
        now = self._clock()
        for t in fetched:
            self._state[t]["lastFetchedAt"] = now

    def checked(self, target_id: str, error: str | None) -> None:
        self._state[target_id].update(lastCheckedAt=self._clock(), lastError=error)

    def status(self) -> dict[str, Any]:
        snap = self._snapshot
        targets = {}
        for t, st in self._state.items():
            hidden = Counter(snap.hidden_by_target.get(t, {}).values())
            targets[t] = {
                "toolVersion": snap.tool_versions.get(t), **st,
                "served": sum(1 for s in snap.served.values() if s.target_id == t),
                "hidden": {r: hidden.get(r, 0) for r in HIDDEN_REASONS},
                "contextMissing": list(snap.context_missing.get(t, [])),
            }
        return {"targets": targets}
