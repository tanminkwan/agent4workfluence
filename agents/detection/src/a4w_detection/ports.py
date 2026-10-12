"""Detection 내부 저장·잠금·시각 interface. 서비스는 이것에만 의존한다."""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager

from a4w_core.models import ChangeSet, SpecVersion


class SpecArchive(ABC):
    """명세 원문 보관 (버전 이력)."""

    @abstractmethod
    def commit(self, target_id: str, raw: bytes, version: str) -> str:
        """원문을 최신으로 보관하고 이력 식별자(커밋)를 돌려준다. 내용이 같으면 기존 것을 돌려준다."""

    @abstractmethod
    def read(self, target_id: str, commit: str) -> bytes: ...


class DetectionStore(ABC):
    """버전 기록·변경분·타겟 상태."""

    @abstractmethod
    def current(self, target_id: str) -> SpecVersion | None:
        """현재 버전. git_commit은 현재 커밋이다."""

    @abstractmethod
    def find_changeset(self, target_id: str, from_version: str | None, to_version: str) -> ChangeSet | None: ...

    @abstractmethod
    def get_changeset(self, target_id: str, change_set_id: str) -> ChangeSet | None: ...

    @abstractmethod
    def record(self, version: SpecVersion, commit: str, changeset: ChangeSet, updated_at: str) -> None:
        """버전 기록(처음일 때만), 변경분, 현재 버전 갱신을 한 트랜잭션으로 한다."""

    @abstractmethod
    def check(self) -> None:
        """보관소에 접근할 수 있는지 확인한다. 안 되면 StorageError."""


class TargetLock(ABC):
    @abstractmethod
    def hold(self, target_id: str) -> AbstractContextManager[None]:
        """잠금을 잡는다. 이미 잡혀 있으면 바로 DetectInProgress."""


class Clock(ABC):
    @abstractmethod
    def now_iso(self) -> str:
        """현재 시각 (ISO 8601, UTC, 초 단위, Z)."""
