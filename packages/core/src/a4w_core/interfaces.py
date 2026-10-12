"""타겟 무관 interface. 서비스는 이것에만 의존하고, 구현은 조립 지점에서 끼운다."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from .models import DocumentChange, OperationChange, ReviewResult


class SpecSource(ABC):
    """타겟에서 명세와 (있으면) 해시를 받는다."""

    @abstractmethod
    def fetch_hash(self) -> str | None:
        """명세 sha256(소문자 16진). 해시를 주지 않는 타겟이면 None."""

    @abstractmethod
    def fetch_spec(self) -> bytes:
        """명세 원문 바이트."""


class SpecParser(ABC):
    """명세 원문을 OpenAPI 문서(dict)로 해석한다."""

    @abstractmethod
    def parse(self, raw: bytes) -> dict[str, Any]: ...


class SpecNormalizer(ABC):
    """줄 단위 비교 전에 명세 원문을 비교 가능한 글로 바꾼다."""

    @abstractmethod
    def to_text(self, raw: bytes) -> str: ...


class ReviewPolicy(ABC):
    """변경분이 사람 확인을 거쳐야 하는지 판단한다 (타겟별 정책)."""

    @abstractmethod
    def evaluate(
        self,
        operations: Sequence[OperationChange],
        document_changes: Sequence[DocumentChange],
    ) -> ReviewResult: ...
