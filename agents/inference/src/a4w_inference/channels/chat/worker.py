"""ChatWorkerPool: 방별 수행 연결 (Phase 5 설계 3·5.1·6절, FR-C13~C16·C21).

dispatcher가 넘긴 요청을 방마다 하나씩 차례로 수행 로직에 접수하고, 결과를 기다려(polling) 방에 답한다. 수행은 얇은 interface
(RequestSubmitter)로만 부르고 보내기는 ReplySender로만 한다. 쓰기가 겹치지 않게 접수한 요청은 다시 접수하지 않는다(시간 초과·재시작 포함).
요청·답 본문은 로그에 남기지 않는다(NFR-C03).
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from .models import IncomingRequest
from .notes import render_channel_notes
from .policy import my_names, strip_mention
from .ports import ChatOutcome, ChatSubmit, ReplySender, RequestSubmitter, SubmitRejected
from .store import SqliteChatState

log = logging.getLogger(__name__)

BUSY_CODES = ("AGENT_BUSY", "CONVERSATION_BUSY")
VALIDATION_CODE = "VALIDATION_ERROR"
REQUIRED_TEXTS = ("answer_empty", "limit_reached", "failed", "rejected", "busy", "queue_full", "timeout", "interrupted")


class ReplyTexts:
    """방에 보내는 고정 문구. 모두 설정에서 온다(코드에 문구를 두지 않는다, NFR-C01)."""

    def __init__(self, texts: Mapping[str, str], *, stop_labels: Mapping[str, str]):
        for key in REQUIRED_TEXTS:
            if not str(texts.get(key, "")).strip():
                raise ValueError(f"채팅 응답 문구가 없다: {key}")
        self._texts = {k: str(texts[k]) for k in REQUIRED_TEXTS}
        self._stop_labels = dict(stop_labels)

    def get(self, key: str) -> str:
        return self._texts[key]

    def stop_label(self, reason: str | None) -> str:
        return self._stop_labels.get(reason or "", "")


@dataclass
class WorkerStats:
    completed: int = 0
    send_failures: int = 0
    errors: int = 0
    queue_full: int = 0
    timeouts: int = 0
    busy_giveups: int = 0


class ChatWorkerPool:
    def __init__(self, *, submitter: RequestSubmitter, sender: ReplySender, state: SqliteChatState, replies: ReplyTexts,
                 now_iso: Callable[[], str], agent_id: str, poll_interval: float, wait_max_seconds: float, busy_retry_seconds: float,
                 busy_retry_max_seconds: float, queue_max: int, notes: str | None = None, notes_members_max: int = 50,
                 notes_name_max_chars: int = 64, mention_name: str | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self._submitter, self._sender, self._state, self._replies = submitter, sender, state, replies
        self._now, self._agent_id = now_iso, agent_id
        self._poll, self._wait_max = poll_interval, wait_max_seconds
        self._busy, self._busy_max = busy_retry_seconds, busy_retry_max_seconds
        self._queue_max, self._notes, self._sleep = queue_max, notes, sleep
        self._notes_members_max, self._notes_name_max = notes_members_max, notes_name_max_chars
        self._mention = mention_name
        self._queues: dict[str, deque[IncomingRequest]] = {}
        self._pending: dict[str, int] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._overflowed: set[str] = set()
        self._background: set[asyncio.Task] = set()
        self.stats = WorkerStats()

    # ------------------------------------------------------------------ 입구 (dispatcher의 sink)

    async def __call__(self, request: IncomingRequest) -> None:
        room, seq = request.room.room_id, request.message.seq
        if self._pending.get(room, 0) >= self._queue_max:
            self.stats.queue_full += 1
            self._set_state(room, seq, "abandoned")
            if room not in self._overflowed:
                self._overflowed.add(room)
                self._spawn(self._notice(room, self._replies.get("queue_full"), f"queue_full:{room}:{seq}"))
            return
        self._queues.setdefault(room, deque()).append(request)
        self._pending[room] = self._pending.get(room, 0) + 1
        if room not in self._tasks:
            self._tasks[room] = asyncio.create_task(self._room_loop(room))

    async def drain(self) -> None:
        while self._tasks or self._background:
            await asyncio.gather(*list(self._tasks.values()), *list(self._background), return_exceptions=True)

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # ------------------------------------------------------------------ 방 하나

    async def _room_loop(self, room: str) -> None:
        queue = self._queues[room]
        try:
            while queue:
                request = queue.popleft()
                try:
                    await self._process(request)
                except Exception as e:                     # 한 요청의 오류가 방의 다음 요청을 막지 않는다
                    self.stats.errors += 1
                    log.error("chat request failed: room=%s seq=%d %s", room[:8], request.message.seq, type(e).__name__)
                    self._set_state(room, request.message.seq, "abandoned")
                finally:
                    self._pending[room] -= 1
        finally:                                           # while이 끝나고 여기까지 await가 없어, 그 사이 새 요청이 끼어들 수 없다
            self._tasks.pop(room, None)
            self._overflowed.discard(room)

    def _text(self, request: IncomingRequest) -> str:
        """호명(설정 이름과 방에서 내 표시 이름)을 뺀 요청 글 (설계 19절)."""
        text = request.message.body
        for name in (*([self._mention] if self._mention else []), *my_names(request.room, request.my_user_id)):
            text = strip_mention(text, name)
        return text.strip()

    async def _process(self, request: IncomingRequest) -> None:
        room, seq = request.room.room_id, request.message.seq
        args = ChatSubmit(agent_id=self._agent_id, text=self._text(request), requester=request.requester,
                          conversation_id=f"chat:{room}", idempotency_key=f"chat:{room}:{seq}", allow_writes=request.allow_writes,
                          channel_notes=render_channel_notes(self._notes, request.room.members, my_user_id=request.my_user_id,
                                                             max_members=self._notes_members_max,
                                                             name_max_chars=self._notes_name_max))
        request_id = await self._submit_with_retry(room, seq, args)
        if request_id is None:
            return
        self._state.set_state(room, seq, "submitted", now=self._now(), request_id=request_id)
        outcome = await self._wait(room, seq, request_id)
        if outcome is None:
            return
        text, normal = self._compose(outcome)
        sent = await self._sender.post(room, text, client_msg_id=f"reply:{request_id}")
        self._finish(room, seq, sent, replied=normal and sent, reply_msg_id=f"reply:{request_id}")
        if sent:
            self.stats.completed += 1
        log.info("chat request done: room=%s seq=%d request=%s status=%s complete=%s sent=%s", room[:8], seq, request_id,
                 outcome.status, outcome.complete, sent)

    async def _submit_with_retry(self, room: str, seq: int, args: ChatSubmit) -> str | None:
        waited = 0.0
        while True:
            try:
                return await self._submitter.submit(args)
            except SubmitRejected as e:
                if e.code in BUSY_CODES:
                    if waited + self._busy > self._busy_max:
                        self.stats.busy_giveups += 1
                        await self._terminal(room, seq, self._replies.get("busy"), f"notice:{room}:{seq}")
                        return None
                    await self._sleep(self._busy)
                    waited += self._busy
                    continue
                text = self._replies.get("rejected") if e.code == VALIDATION_CODE else f"{self._replies.get('failed')} ({e.code})"
                await self._terminal(room, seq, text, f"notice:{room}:{seq}")
                return None

    async def _wait(self, room: str, seq: int, request_id: str) -> ChatOutcome | None:
        waited = 0.0
        while True:
            try:
                outcome = await self._submitter.get(request_id)
            except Exception as e:
                self.stats.errors += 1
                log.error("chat request lookup failed: request=%s %s", request_id, type(e).__name__)
                await self._terminal(room, seq, f"{self._replies.get('failed')} (INTERNAL)", f"notice:{room}:{seq}")
                return None
            if outcome is None:
                await self._terminal(room, seq, f"{self._replies.get('failed')} (REQUEST_LOST)", f"notice:{room}:{seq}")
                return None
            if outcome.status != "running":
                return outcome
            if waited + self._poll > self._wait_max:
                self.stats.timeouts += 1
                await self._terminal(room, seq, self._replies.get("timeout"), f"timeout:{request_id}")
                return None
            await self._sleep(self._poll)
            waited += self._poll

    def _compose(self, outcome: ChatOutcome) -> tuple[str, bool]:
        """(방에 보낼 글, 정상 결과인가)."""
        if outcome.status == "done":
            if outcome.complete:
                answer = (outcome.answer or "").strip()
                return (answer or self._replies.get("answer_empty")), True
            label = self._replies.stop_label(outcome.stop_reason)
            limit = self._replies.get("limit_reached")
            return (f"{limit} {label}" if label else limit), True
        if outcome.status == "failed":
            return f"{self._replies.get('failed')} ({outcome.error_code or 'UNKNOWN'})", True
        return f"{self._replies.get('failed')} (UNKNOWN_STATUS)", False

    # ------------------------------------------------------------------ 보내기·상태

    async def _terminal(self, room: str, seq: int, text: str, client_msg_id: str) -> None:
        """접수 단계에서 끝난 요청: 안내를 보내고 행을 abandoned로 닫는다."""
        sent = await self._sender.post(room, text, client_msg_id=client_msg_id)
        self._finish(room, seq, sent, replied=False, reply_msg_id=client_msg_id)

    def _finish(self, room: str, seq: int, sent: bool, *, replied: bool, reply_msg_id: str) -> None:
        if not sent:
            self.stats.send_failures += 1
        self._state.set_state(room, seq, "replied" if replied else "abandoned", now=self._now(),
                              reply_msg_id=reply_msg_id if sent else None)

    def _set_state(self, room: str, seq: int, state: str) -> None:
        try:
            self._state.set_state(room, seq, state, now=self._now())
        except KeyError:
            pass

    async def _notice(self, room: str, text: str, client_msg_id: str) -> None:
        try:
            if not await self._sender.post(room, text, client_msg_id=client_msg_id):
                self.stats.send_failures += 1
        except Exception as e:
            self.stats.errors += 1
            log.error("chat notice failed: room=%s %s", room[:8], type(e).__name__)

    # ------------------------------------------------------------------ 재시작 복구

    async def recover(self) -> int:
        """재시작으로 끊긴 요청(큐에 넣었으나 답 전)을 닫고, 방마다 한 번 알린다. 다시 수행하지 않는다(쓰기 중복 방지)."""
        rows = self._state.open_requests()
        last_seq: dict[str, int] = {}
        for row in rows:
            last_seq[row.room_id] = max(last_seq.get(row.room_id, 0), row.seq)
        for room, seq in last_seq.items():
            info = self._state.get_room(room)
            if info is not None and info.closed_at is None:
                await self._notice(room, self._replies.get("interrupted"), f"interrupted:{room}:{seq}")
        for row in rows:
            self._set_state(row.room_id, row.seq, "abandoned")
        if rows:
            log.warning("chat requests interrupted by restart: %d", len(rows))
        return len(rows)
