"""명세 받기. 해시 URL 방식만 구현하고 해시 없는 방식은 자리만 둔다."""

from __future__ import annotations

import re

import httpx

from .errors import SpecFetchFailed, UnsupportedSpecSource
from .interfaces import SpecSource

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class HashUrlSpecSource(SpecSource):
    def __init__(self, *, spec_url: str, hash_url: str, max_bytes: int, client: httpx.Client):
        self._spec_url = spec_url
        self._hash_url = hash_url
        self._max_bytes = max_bytes
        self._client = client

    def _get(self, url: str, limit: int) -> bytes:
        try:
            with self._client.stream("GET", url) as resp:
                if not 200 <= resp.status_code < 300:
                    raise SpecFetchFailed(f"응답 {resp.status_code}: {url}")
                buf = bytearray()
                for chunk in resp.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > limit:
                        raise SpecFetchFailed(f"크기 한도({limit} bytes) 초과: {url}")
                return bytes(buf)
        except httpx.HTTPError as e:
            raise SpecFetchFailed(f"접속 실패({type(e).__name__}): {url}") from None

    def fetch_hash(self) -> str:
        body = self._get(self._hash_url, self._max_bytes)
        value = body.decode("ascii", errors="replace").strip().lower()
        if not _HEX64.match(value):
            raise SpecFetchFailed(f"해시 형식이 아니다: {self._hash_url}")
        return value

    def fetch_spec(self) -> bytes:
        return self._get(self._spec_url, self._max_bytes)


class NoHashSpecSource(SpecSource):
    """자리만 둔다: 해시 URL이 없는 타겟 (명세를 받아 직접 해시를 계산하는 방식)."""

    def fetch_hash(self) -> str | None:
        raise UnsupportedSpecSource("해시 URL이 없는 명세 받기는 아직 구현하지 않았다")

    def fetch_spec(self) -> bytes:
        raise UnsupportedSpecSource("해시 URL이 없는 명세 받기는 아직 구현하지 않았다")
