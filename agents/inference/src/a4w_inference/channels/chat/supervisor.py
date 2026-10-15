"""ConnectionSupervisor: 연결 유지와 재연결 (Phase 5 설계 4.1절, FR-C01·C02).

연결이 끊기면 지수 backoff(지터)로 다시 붙는다. hello를 받으면 backoff를 처음으로 되돌린다. 다시 붙어도 소용없는 끝남(4401·401·403·
4400·404)은 상태를 올리고 가장 긴 간격으로 쉬며, 4409가 연달아 나오면(다른 곳의 같은 계정 연결과 서로 밀어내는 중) 충돌로 보고 쉰다.
handler 오류는 그 연결을 닫고 다시 붙게 한다(놓친 것은 hello 뒤 보충이 채운다). 어떤 프레임에도 본문은 로그로 남기지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .models import BadFrame, Frame, HelloFrame, UnknownFrame
from .ws_client import ConnectionEnded

log = logging.getLogger(__name__)

CLOSE_PROTOCOL, CLOSE_AUTH, CLOSE_CONFLICT = 4400, 4401, 4409


@dataclass
class SupervisorStats:
    unknown_frames: int = 0
    bad_frames: int = 0
    reconnects: int = 0
    handler_errors: int = 0
    last_end: str | None = None


def _default_jitter(delay: float) -> float:
    return delay * (1 + random.uniform(0, 0.2))


class ConnectionSupervisor:
    def __init__(self, connection_factory: Callable[[], Any], handler: Callable[[Frame], Awaitable[None]], *, backoff_initial: float,
                 backoff_max: float, jitter: Callable[[float], float] | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, conflict_limit: int = 3):
        self._factory, self._handler = connection_factory, handler
        self._initial, self._max = backoff_initial, backoff_max
        self._jitter = jitter or _default_jitter
        self._sleep = sleep
        self._conflict_limit = conflict_limit
        self._stopping = False
        self.state = "idle"
        self.stats = SupervisorStats()

    def stop(self) -> None:
        self._stopping = True

    async def run(self) -> None:
        current, conflicts = self._initial, 0
        try:
            while not self._stopping:
                self.state = "connecting"
                saw_hello = False
                end: ConnectionEnded | None = None
                try:
                    conn = self._factory()
                except Exception as e:
                    log.warning("chat connection factory failed: %s", type(e).__name__)
                    end = ConnectionEnded("network")
                else:
                    frames = conn.frames()
                    try:
                        async for frame in frames:
                            if isinstance(frame, UnknownFrame):
                                self.stats.unknown_frames += 1
                                continue
                            if isinstance(frame, BadFrame):
                                self.stats.bad_frames += 1
                                continue
                            if isinstance(frame, HelloFrame):
                                saw_hello = True
                                self.state = "connected"
                                current, conflicts = self._initial, 0
                            try:
                                await self._handler(frame)
                            except Exception as e:
                                self.stats.handler_errors += 1
                                self.stats.last_end = "handler_error"
                                log.warning("chat frame handler failed: %s", type(e).__name__)
                                break
                            if self._stopping:
                                break
                    except ConnectionEnded as e:
                        end = e
                    finally:
                        try:
                            await frames.aclose()
                        except Exception:       # 닫다가 난 오류는 이미 끝난 연결의 것이다
                            pass
                if self._stopping:
                    break
                if end is not None:
                    self.stats.last_end = end.label
                self.stats.reconnects += 1
                delay, state, conflicts = self._plan(end, current, conflicts)
                self.state = state
                log.info("chat connection ended: %s state=%s retry_in=%.1fs", self.stats.last_end, state, delay)
                await self._sleep(delay)
                current = self._max if state != "reconnecting" and state != "connecting" else min(current * 2, self._max)
        finally:
            self.state = "stopped"

    def _plan(self, end: ConnectionEnded | None, current: float, conflicts: int) -> tuple[float, str, int]:
        """(기다릴 시간, 상태, 연속 4409 횟수)."""
        if end is not None:
            if end.kind == "closed" and end.code == CLOSE_AUTH or end.kind == "rejected" and end.http_status in (401, 403):
                return self._jitter(self._max), "auth_failed", 0
            if end.kind == "closed" and end.code == CLOSE_PROTOCOL:
                return self._jitter(self._max), "protocol_error", 0
            if end.kind == "rejected" and end.http_status == 404:
                return self._jitter(self._max), "chat_disabled", 0
            if end.kind == "closed" and end.code == CLOSE_CONFLICT:
                conflicts += 1
                if conflicts >= self._conflict_limit:
                    return self._jitter(self._max), "conflict", conflicts
                return self._jitter(current), "reconnecting", conflicts
        return self._jitter(current), "reconnecting", 0
