"""줄 단위 비교용 정규화. 원문 그대로(키 정렬 명세용)만 구현하고 정규화는 자리만 둔다."""

from __future__ import annotations

from .errors import UnsupportedSpecSource
from .interfaces import SpecNormalizer


class IdentityNormalizer(SpecNormalizer):
    def to_text(self, raw: bytes) -> str:
        return raw.decode("utf-8", errors="replace")


class CanonicalNormalizer(SpecNormalizer):
    """자리만 둔다: 키 정렬이 보장되지 않는 명세."""

    def to_text(self, raw: bytes) -> str:
        raise UnsupportedSpecSource("명세 정규화는 아직 구현하지 않았다")
