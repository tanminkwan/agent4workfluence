"""키 단위 배타 잠금 (flock, 기다리지 않음). 프로세스가 죽으면 운영체제가 푼다.

Detection(변경 식별·검증)과 Manufacture(반영)가 같이 쓴다. 잠겨 있을 때 낼 오류는 쓰는 쪽이 정한다.
"""

from __future__ import annotations

import fcntl
import os
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from .errors import A4WError, StorageError

_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,126}$")


class TargetLock(ABC):
    @abstractmethod
    def hold(self, key: str) -> AbstractContextManager[None]:
        """잠금을 잡는다. 이미 잡혀 있으면 바로 오류."""


class HeldLock:
    """잡은 잠금. 다른 스레드에서 풀 수 있다."""

    def __init__(self, fd: int):
        self._fd: int | None = fd

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None


class FileTargetLock(TargetLock):
    def __init__(self, lock_dir: Path | str, *, busy: Callable[[str], A4WError]):
        self._dir = Path(lock_dir)
        self._busy = busy

    def acquire(self, key: str) -> HeldLock:
        if not _KEY.match(key):
            raise ValueError(f"잠금 이름 형식이 아니다: {key!r}")
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._dir / f"{key}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as e:
            raise StorageError(f"잠금 파일을 열 수 없다: {type(e).__name__}") from None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise self._busy(key) from None
        return HeldLock(fd)

    @contextmanager
    def hold(self, key: str) -> Iterator[None]:
        held = self.acquire(key)
        try:
            yield
        finally:
            held.release()
