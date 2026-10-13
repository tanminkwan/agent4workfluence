"""실호출 검증 환경 interface와, tool이 아닌 타겟 호출(준비·정리)용 RawCaller (Phase 2 설계 11.6절).

준비·정리 호출도 tool 실행과 같은 TargetHttpClient·CallPolicy를 쓴다 (호출 한도를 함께 센다).
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .call_policy import CallPolicy
from .interfaces import GenericErrorInterpreter
from .request import HttpRequest
from .target_http import TargetHttpClient


class CallFailed(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class RawResponse:
    status: int
    data: Any


class RawCaller:
    def __init__(self, http: TargetHttpClient, policy: CallPolicy):
        self._http = http
        self._policy = policy

    def call(self, method: str, path: str, *, query: Mapping[str, str] | None = None, json: Any = None) -> RawResponse:
        req = HttpRequest(method=method, path=path, query=dict(query or {}), json_body=json)
        outcome = self._policy.run(lambda: self._http.send(req), write=req.is_write)
        if outcome.response is None:
            raise CallFailed(f"{method} {path}: {outcome.error.code}")
        resp = outcome.response
        return RawResponse(resp.status_code, _json(resp.content))

    def expect(self, method: str, path: str, *, ok: tuple[int, ...], query=None, json=None) -> Any:
        r = self.call(method, path, query=query, json=json)
        if r.status not in ok:
            raw = r.data if isinstance(r.data, (bytes, str)) else _dumps(r.data)
            err = GenericErrorInterpreter().interpret(r.status, {}, raw.encode() if isinstance(raw, str) else raw)
            raise CallFailed(f"{method} {path}: {r.status} {err.target_code or ''} {err.message}".strip(), r.status)
        return r.data


def _json(content: bytes) -> Any:
    if not content:
        return None
    try:
        return json.loads(content)
    except (ValueError, UnicodeDecodeError):
        return content.decode("utf-8", errors="replace")


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


@dataclass
class SandboxContext:
    """시나리오에서 `${sandbox.<key>}`로 쓰는 값과, 정리할 때 필요한 내부 값."""

    values: dict[str, Any]
    internal: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CleanupReport:
    ok: bool
    failures: tuple[str, ...] = ()


class VerifySandbox(ABC):
    @abstractmethod
    def context_keys(self) -> list[str]:
        """prepare가 돌려주는 값 이름 (`${sandbox.*}`)."""

    @abstractmethod
    def prepare(self, caller: RawCaller, run_id: str) -> SandboxContext:
        """시험 위치와 바탕 자료를 만든다. 실패하면 만든 것을 지우고 SandboxUnavailable."""

    @abstractmethod
    def cleanup(self, caller: RawCaller, context: SandboxContext) -> CleanupReport:
        """만든 것을 지운다. 실패는 예외가 아니라 보고로 돌려준다."""
