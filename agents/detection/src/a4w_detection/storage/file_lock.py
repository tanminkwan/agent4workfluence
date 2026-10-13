"""변경 식별 잠금. 구현은 core의 FileTargetLock이고, 잠겨 있으면 DETECT_IN_PROGRESS다 (설계 8.3절)."""

from __future__ import annotations

from pathlib import Path

from a4w_core.errors import DetectInProgress
from a4w_core.locking import FileTargetLock as _CoreFileTargetLock


def _busy(target_id: str) -> DetectInProgress:
    return DetectInProgress(f"변경 식별이 진행 중이다: {target_id}")


class FileTargetLock(_CoreFileTargetLock):
    def __init__(self, lock_dir: Path | str):
        super().__init__(lock_dir, busy=_busy)
