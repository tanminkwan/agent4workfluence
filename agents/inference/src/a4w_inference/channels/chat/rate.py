"""RateLimiter: 보내기 속도 제한 (Phase 5 설계 5.2절 4번, FR-C27).

서버 한도는 사용자 단위 분당 60이다. 토큰 버킷은 창 경계에서 한도의 두 배까지 보낼 수 있어 맞지 않아,
어느 window_seconds 구간에도 max_per_window건을 넘지 않는 미끄러지는 창으로 센다. 기다리는 쪽은 도착 순서대로 처리한다.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable


class RateLimiter:
    def __init__(self, *, max_per_window: int, window_seconds: float = 60.0, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        if max_per_window < 1 or window_seconds <= 0:
            raise ValueError("보내기 속도 제한 설정이 맞지 않는다")
        self._max, self._window = max_per_window, window_seconds
        self._clock, self._sleep = clock, sleep
        self._stamps: deque[float] = deque()
        self._lock = asyncio.Lock()               # 도착 순서대로 한 번에 하나씩

    def _trim(self, now: float) -> None:
        while self._stamps and self._stamps[0] <= now - self._window:
            self._stamps.popleft()

    def in_window(self) -> int:
        self._trim(self._clock())
        return len(self._stamps)

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                self._trim(now)
                if len(self._stamps) < self._max:
                    self._stamps.append(now)
                    return
                await self._sleep(max(self._stamps[0] + self._window - now, 0.0))
