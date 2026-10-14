"""정의를 받는 곳 (Phase 3 설계 6.1절). 구현은 Manufacture tool 정의 조회 API 하나다(FR-T52).

이 client는 Manufacture API token만 갖는다. 타겟 token은 다른 객체(TargetHttpClient)에 있다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx


class ToolsetUnavailable(RuntimeError):
    """조회 API에 닿지 못했거나 이상한 응답을 받았다. 가진 묶음으로 계속한다."""


class ToolsetMissing(LookupError):
    """타겟은 있지만 아직 tool 버전이 없다 (TOOLSET_NOT_FOUND)."""


@dataclass(frozen=True)
class VersionAnswer:
    tool_version: str | None   # None: 304 (바뀌지 않음)
    etag: str | None


class ToolsetSource(ABC):
    @abstractmethod
    def current_version(self, target_id: str, etag: str | None) -> VersionAnswer: ...

    @abstractmethod
    def fetch(self, target_id: str, tool_version: str) -> dict[str, Any]: ...


class ManufactureToolsetSource(ToolsetSource):
    def __init__(self, *, base_url: str, token: str | None, timeout: float,
                 transport: httpx.BaseTransport | None = None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        # 내부 주소다. proxy는 환경변수(NO_PROXY에 manufacture)를 따른다
        self._client = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout,
                                    transport=transport, trust_env=transport is None)

    def current_version(self, target_id: str, etag: str | None) -> VersionAnswer:
        headers = {"If-None-Match": etag} if etag else {}
        resp = self._get(f"/v1/targets/{target_id}/toolset/current", headers)
        if resp.status_code == 304:
            return VersionAnswer(None, etag)
        if resp.status_code == 404 and _code(resp) == "TOOLSET_NOT_FOUND":
            raise ToolsetMissing(f"아직 tool 버전이 없다: {target_id}")
        body = self._json(resp)
        version = body.get("toolVersion") if isinstance(body, dict) else None
        if not isinstance(version, str):
            raise ToolsetUnavailable("현재 버전 응답에 toolVersion이 없다")
        return VersionAnswer(version, resp.headers.get("etag"))

    def fetch(self, target_id: str, tool_version: str) -> dict[str, Any]:
        body = self._json(self._get(f"/v1/targets/{target_id}/toolsets/{tool_version}", {}))
        if not isinstance(body, dict):
            raise ToolsetUnavailable("묶음 응답이 객체가 아니다")
        return body

    def _get(self, path: str, headers: dict[str, str]) -> httpx.Response:
        try:
            return self._client.get(path, headers=headers)
        except httpx.HTTPError as e:
            raise ToolsetUnavailable(f"조회 API 접속 실패: {type(e).__name__}") from None

    @staticmethod
    def _json(resp: httpx.Response) -> Any:
        if resp.status_code != 200:
            raise ToolsetUnavailable(f"조회 API 응답 {resp.status_code} {_code(resp) or ''}".strip())
        try:
            return resp.json()
        except ValueError:
            raise ToolsetUnavailable("조회 API 응답이 JSON이 아니다") from None

    def __repr__(self) -> str:  # token을 드러내지 않는다
        return f"ManufactureToolsetSource(base_url={str(self._client.base_url)!r})"


def _code(resp: httpx.Response) -> str | None:
    try:
        return resp.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return None
