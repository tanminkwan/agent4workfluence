"""호출 규약: 호출 한도, 429·5xx·시간 초과 처리 (설계 15.2절).

타겟 설정 값만으로 동작하는 일반 구현이다. 409는 다시 하지 않는다(덮어쓰기 방지).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Protocol

import httpx

from .errors import OUTCOME_UNKNOWN, RATE_LIMITED, TARGET_UNAVAILABLE, TIMEOUT, ToolError

WINDOW_SECONDS = 60.0  # 한도는 "분당 N회"로 정의된다


class Clock(Protocol):
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@dataclass(frozen=True)
class CallOutcome:
    response: httpx.Response | None
    error: ToolError | None
    attempts: int


class CallPolicy(ABC):
    @abstractmethod
    def run(self, send: Callable[[], httpx.Response], *, write: bool) -> CallOutcome:
        """send를 규약대로 부른다. 받은 응답(성공·실패 상태 모두) 또는 규약 오류를 돌려준다."""


class ConfigCallPolicy(CallPolicy):
    def __init__(
        self,
        *,
        read_per_minute: int,
        write_per_minute: int,
        max_attempts: int,
        max_retry_wait_seconds: float,
        clock: Clock | None = None,
    ):
        if min(read_per_minute, write_per_minute, max_attempts) < 1:
            raise ValueError("한도와 시도 수는 1 이상이어야 한다")
        self._limits = {False: read_per_minute, True: write_per_minute}
        self._windows: dict[bool, deque[float]] = {False: deque(), True: deque()}
        self._max_attempts = max_attempts
        self._max_wait = max_retry_wait_seconds
        self._clock = clock or SystemClock()
        self._lock = Lock()

    @classmethod
    def from_settings(cls, cfg) -> "ConfigCallPolicy":
        return cls(
            read_per_minute=cfg.require_int("CALL_READ_PER_MINUTE"),
            write_per_minute=cfg.require_int("CALL_WRITE_PER_MINUTE"),
            max_attempts=cfg.require_int("CALL_MAX_ATTEMPTS"),
            max_retry_wait_seconds=cfg.require_int("CALL_MAX_RETRY_WAIT_SECONDS"),
        )

    def run(self, send: Callable[[], httpx.Response], *, write: bool) -> CallOutcome:
        attempts = 0
        last: httpx.Response | None = None
        while attempts < self._max_attempts:
            attempts += 1
            self._take_slot(write)
            try:
                last = send()
            except httpx.ConnectError:
                # 보내기 전에 끊겼으므로 쓰기도 다시 해도 안전하다
                if attempts < self._max_attempts:
                    continue
                return CallOutcome(None, ToolError(TARGET_UNAVAILABLE, "타겟에 접속하지 못했다", retryable=True), attempts)
            except (httpx.TimeoutException, httpx.TransportError):
                if write:
                    return CallOutcome(
                        None,
                        ToolError(
                            OUTCOME_UNKNOWN,
                            "응답을 받지 못했다. 요청이 처리됐을 수 있다",
                            hint="만들어졌거나 바뀌었을 수 있다. 목록·조회로 확인한 뒤 다시 하라",
                        ),
                        attempts,
                    )
                if attempts < self._max_attempts:
                    continue
                return CallOutcome(None, ToolError(TIMEOUT, "타겟 응답 시간 초과", retryable=True), attempts)

            status = last.status_code
            if status == 429:
                wait = _retry_after(last)
                if wait is None or wait > self._max_wait or attempts >= self._max_attempts:
                    return CallOutcome(
                        last,
                        ToolError(RATE_LIMITED, "타겟 호출 한도에 걸렸다", http_status=429, retryable=True,
                                  hint=f"{wait if wait is not None else '잠시'}초 뒤에 다시 하라"),
                        attempts,
                    )
                self._clock.sleep(wait)
                continue
            if status >= 500 and not write and attempts < self._max_attempts:
                continue
            return CallOutcome(last, None, attempts)
        return CallOutcome(last, None, attempts)

    def _take_slot(self, write: bool) -> None:
        with self._lock:
            window, limit = self._windows[write], self._limits[write]
            now = self._clock.monotonic()
            while window and now - window[0] >= WINDOW_SECONDS:
                window.popleft()
            if len(window) >= limit:
                wait = WINDOW_SECONDS - (now - window[0])
                self._clock.sleep(wait)
                now = self._clock.monotonic()
                while window and now - window[0] >= WINDOW_SECONDS:
                    window.popleft()
            window.append(now)


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None
