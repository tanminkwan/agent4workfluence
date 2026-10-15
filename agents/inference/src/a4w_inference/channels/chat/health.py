"""채팅 상태 (Phase 5 설계 11.2절, FR-C42). /health의 chat 항목.

연결 상태·카운터만 담는다. token·메시지 본문·사용자 이름·방 id는 담지 않는다. 채팅이 나빠도 /health 전체 status는 내리지 않는다.
채팅 API 변경 감지는 하지 않는다(사용자 확정 2026-10-15).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .models import Frame


class ChatHealth:
    def __init__(self, *, supervisor, dispatcher, worker, state, now_iso: Callable[[], str]):
        self._supervisor, self._dispatcher, self._worker, self._state, self._now = supervisor, dispatcher, worker, state, now_iso
        self._last_frame_at: str | None = None

    @staticmethod
    def disabled() -> dict[str, Any]:
        return {"state": "disabled"}

    def track(self, handler: Callable[[Frame], Awaitable[None]]) -> Callable[[Frame], Awaitable[None]]:
        """처리 결과와 상관없이 프레임이 온 시각을 남기는 wrapper."""
        async def wrapped(frame: Frame) -> None:
            self._last_frame_at = self._now()
            await handler(frame)
        return wrapped

    def snapshot(self) -> dict[str, Any]:
        s, d, w = self._supervisor.stats, self._dispatcher.stats, self._worker.stats
        return {
            "state": self._supervisor.state, "rooms": len(self._state.open_rooms()), "lastFrameAt": self._last_frame_at,
            "lastEnd": s.last_end, "reconnects": s.reconnects, "handlerErrors": s.handler_errors, "unknownFrames": s.unknown_frames,
            "badFrames": s.bad_frames, "unknownRoomChanges": d.unknown_room_changes, "catchupFailures": d.catchup_failures,
            "staleSkipped": d.stale_skipped, "sinkErrors": d.sink_errors,
            "worker": {"completed": w.completed, "sendFailures": w.send_failures, "errors": w.errors, "queueFull": w.queue_full,
                       "timeouts": w.timeouts, "busyGiveups": w.busy_giveups},
        }
