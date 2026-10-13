"""RagStore interface (Phase 2 설계 14.1절, FR-297)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class RagError(Exception):
    pass


@dataclass(frozen=True)
class RagSpace:
    collection: str
    domain: str
    description: str


@dataclass(frozen=True)
class RagSpaceRef:
    collection: str
    domain_id: int


@dataclass(frozen=True)
class RagDocument:
    id: str
    content: str            # 임베딩 대상
    extended_content: str   # 검색 결과로 그대로 돌아오는 상세
    source: str


@dataclass(frozen=True)
class RagHit:
    id: str
    score: float
    content: str
    extended_content: str
    source: str


class RagStore(ABC):
    @abstractmethod
    def ensure_space(self, space: RagSpace) -> RagSpaceRef: ...

    @abstractmethod
    def upsert(self, ref: RagSpaceRef, doc: RagDocument) -> None: ...

    @abstractmethod
    def delete(self, ref: RagSpaceRef, doc_id: str) -> None: ...

    @abstractmethod
    def list_ids(self, ref: RagSpaceRef, *, limit: int) -> dict[str, str]:
        """id → source."""

    @abstractmethod
    def search(self, ref: RagSpaceRef, query: str, *, limit: int) -> list[RagHit]: ...
