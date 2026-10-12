"""타겟 단위 배타 잠금 (flock, 기다리지 않음). 프로세스가 죽으면 운영체제가 푼다 (설계 8.3절)."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from a4w_core.errors import DetectInProgress, StorageError

from ..ports import TargetLock


class FileTargetLock(TargetLock):
    def __init__(self, lock_dir: Path | str):
        self._dir = Path(lock_dir)

    @contextmanager
    def hold(self, target_id: str) -> Iterator[None]:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._dir / f"{target_id}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as e:
            raise StorageError(f"잠금 파일을 열 수 없다: {type(e).__name__}") from None
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise DetectInProgress(f"변경 식별이 진행 중이다: {target_id}") from None
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
