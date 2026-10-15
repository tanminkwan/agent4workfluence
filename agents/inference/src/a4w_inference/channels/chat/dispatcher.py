"""ChatDispatcher: 프레임·보충 → 중복 제거 → 요청 판단 → 요청 전달 (Phase 5 설계 3·4.2·4.3절, FR-C03~C05·C13).

프레임은 감독이 한 번에 하나씩 넘기므로 hello 처리(보충)가 끝나야 다음 실시간 프레임이 처리된다. 같은 메시지는 상태 저장소의
(roomId, seq)로 한 번만 받는다. 본문은 로그에 남기지 않는다(NFR-C03).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from .models import ChatMessage, Frame, HelloFrame, IncomingRequest, MessageFrame, RoomFrame, RoomInfo
from .policy import ReplyPolicy, requester_label
from .ports import ChatApi, ChatApiError
from .store import SqliteChatState

log = logging.getLogger(__name__)
KIND_LEAVE = "leave"


@dataclass
class DispatcherStats:
    catchup_failures: int = 0
    stale_skipped: int = 0
    unknown_room_changes: int = 0
    sink_errors: int = 0
    dropped_before_hello: int = 0


class ChatDispatcher:
    def __init__(self, *, api: ChatApi, state: SqliteChatState, policy: ReplyPolicy,
                 sink: Callable[[IncomingRequest], Awaitable[None]], clock: Callable[[], datetime], now_iso: Callable[[], str],
                 catchup_page: int = 200, catchup_max_age_seconds: float = 86400):
        self._api, self._state, self._policy, self._sink = api, state, policy, sink
        self._clock, self._now_iso = clock, now_iso
        self._page, self._max_age = catchup_page, catchup_max_age_seconds
        self._me: str | None = None
        self._rooms: dict[str, RoomInfo] = {}
        self.stats = DispatcherStats()

    @property
    def my_user_id(self) -> str | None:
        return self._me

    async def handle(self, frame: Frame) -> None:
        if isinstance(frame, HelloFrame):
            await self._on_hello(frame)
        elif isinstance(frame, MessageFrame):
            await self._consume(frame.message, catchup=False)
        elif isinstance(frame, RoomFrame):
            await self._on_room(frame)

    # ------------------------------------------------------------------ hello · 보충

    async def _on_hello(self, hello: HelloFrame) -> None:
        self._me = hello.user_id
        rooms = await self._api.rooms()                          # 실패하면 올려 보낸다(감독이 다시 붙는다)
        self._rooms = {r.room_id: r for r in rooms}
        now = self._now_iso()
        catch_up: list[RoomInfo] = []
        for room in rooms:
            known = self._state.get_room(room.room_id)
            was_open = known is not None and known.closed_at is None
            self._state.register_room(room.room_id, room.kind, start_seq=room.last_seq, now=now)   # 처음 보는 방은 현재 seq에서 시작
            if was_open:
                catch_up.append(room)
        listed = set(self._rooms)
        for known in self._state.open_rooms():
            if known.room_id not in listed:                      # 오프라인 동안 나간 방
                self._state.close_room(known.room_id, now=now)
        for room in catch_up:
            try:
                await self._catch_up(room)
            except ChatApiError as e:
                self.stats.catchup_failures += 1
                log.warning("chat catch-up failed: room=%s code=%s", room.room_id[:8], e.code)

    async def _catch_up(self, room: RoomInfo) -> None:
        after = self._state.last_seq(room.room_id) or 0
        while True:
            page = await self._api.messages(room.room_id, after_seq=after, limit=self._page)
            if not page:
                return
            for message in page:
                await self._consume(message, catchup=True)
            if page[-1].seq <= after or len(page) < self._page:
                return
            after = page[-1].seq

    # ------------------------------------------------------------------ room 프레임

    async def _on_room(self, frame: RoomFrame) -> None:
        if frame.change not in ("joined", "members"):
            self.stats.unknown_room_changes += 1
            log.info("chat room change ignored: %s", frame.change)
            return
        room = await self._api.room(frame.room_id)
        self._rooms[room.room_id] = room
        known = self._state.get_room(room.room_id)
        if frame.change == "joined" or known is None:
            self._state.register_room(room.room_id, room.kind, start_seq=room.last_seq, now=self._now_iso())

    # ------------------------------------------------------------------ 메시지

    async def _consume(self, message: ChatMessage, *, catchup: bool) -> None:
        if self._me is None:
            self.stats.dropped_before_hello += 1           # 내 id를 모르면 자기 메시지를 가릴 수 없다. hello 뒤 보충이 받는다
            return
        known = self._state.get_room(message.room_id)
        if known is not None and message.seq <= known.last_seq:
            return                                          # 이미 처리했다
        room = await self._room_info(message.room_id)
        if known is None:                                   # room 프레임보다 message가 먼저 온 방: 이 메시지부터 우리 몫이다
            self._state.register_room(room.room_id, room.kind, start_seq=message.seq - 1, now=self._now_iso())
        elif known.closed_at is not None:
            return
        now = self._now_iso()
        if message.kind == KIND_LEAVE and message.sender_id == self._me:
            if self._state.begin(message.room_id, message.seq, "skipped", now=now):
                self._state.close_room(message.room_id, now=now)
                self._rooms.pop(message.room_id, None)
                log.info("left chat room: room=%s", message.room_id[:8])
            return
        if catchup and self._is_stale(message):
            if self._state.begin(message.room_id, message.seq, "skipped", now=now):
                self.stats.stale_skipped += 1
            return
        decision = self._policy.decide(message, room, my_user_id=self._me)
        if not decision.is_request:
            self._state.begin(message.room_id, message.seq, "skipped", now=now)
            return
        if not self._state.begin(message.room_id, message.seq, "queued", now=now):
            return
        request = IncomingRequest(room=room, message=message, requester=requester_label(message.sender_id, room),
                                  allow_writes=decision.allow_writes)
        log.info("chat request: room=%s seq=%d reason=%s writes=%s chars=%d", message.room_id[:8], message.seq, decision.reason,
                 decision.allow_writes, len(message.body))
        try:
            await self._sink(request)
        except Exception as e:
            self.stats.sink_errors += 1
            log.error("chat request sink failed: room=%s seq=%d %s", message.room_id[:8], message.seq, type(e).__name__)

    async def _room_info(self, room_id: str) -> RoomInfo:
        room = self._rooms.get(room_id)
        if room is None:
            room = await self._api.room(room_id)              # 실패하면 올려 보낸다(이 메시지는 아직 처리 전이라 보충이 다시 준다)
            self._rooms[room_id] = room
        return room

    def _is_stale(self, message: ChatMessage) -> bool:
        try:
            created = datetime.fromisoformat(message.created_at)
        except ValueError:
            return True
        if created.tzinfo is None:
            return True
        return (self._clock() - created).total_seconds() > self._max_age
