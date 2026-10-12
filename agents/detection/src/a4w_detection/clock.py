from __future__ import annotations

from datetime import UTC, datetime

from .ports import Clock


class SystemClock(Clock):
    def now_iso(self) -> str:
        return datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
