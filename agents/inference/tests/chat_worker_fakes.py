"""수행 연결(worker) 시험용 가짜들 (Phase 5 설계 5.1절). 수행 로직 쪽 RequestSubmitter와 보내기 쪽 ReplySender의 가짜다."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from a4w_inference.channels.chat.ports import ChatOutcome, ChatSubmit, ReplySender, RequestSubmitter, SubmitRejected


class FakeSubmitter(RequestSubmitter):
    """수행 연결 시험용. submit은 request_id를 주고, get은 outcome_fn이 정한 결과를 준다. hold()로 한 대화의 결과를 붙잡을 수 있다."""

    def __init__(self):
        self.submits: list[ChatSubmit] = []
        self.submit_errors: list[SubmitRejected] = []          # 앞에서부터 하나씩 던지고, 다 쓰면 성공한다
        self.always_error: SubmitRejected | None = None
        self.get_calls: list[str] = []
        self.get_error: Exception | None = None
        self._polls: dict[str, int] = {}
        self._holds: dict[str, Any] = {}
        self._conv_of: dict[str, str] = {}
        self.outcome_fn: Callable[[str, int], ChatOutcome | None] = lambda rid, n: ChatOutcome("done", True, None, f"answer-for-{rid}", None)

    def hold(self, conversation_id: str):
        import asyncio
        self._holds[conversation_id] = asyncio.Event()
        return self._holds[conversation_id]

    async def submit(self, args: ChatSubmit) -> str:
        import asyncio
        await asyncio.sleep(0)
        if self.always_error is not None:
            raise self.always_error
        if self.submit_errors:
            raise self.submit_errors.pop(0)
        self.submits.append(args)
        rid = f"r-{len(self.submits):04d}"
        self._conv_of[rid] = args.conversation_id
        return rid

    async def get(self, request_id: str) -> ChatOutcome | None:
        import asyncio
        await asyncio.sleep(0)
        self.get_calls.append(request_id)
        if self.get_error is not None:
            raise self.get_error
        n = self._polls[request_id] = self._polls.get(request_id, 0) + 1
        hold = self._holds.get(self._conv_of.get(request_id, ""))
        if hold is not None and not hold.is_set():
            return ChatOutcome("running", None, None, None, None)
        return self.outcome_fn(request_id, n)


class FakeSender(ReplySender):
    def __init__(self):
        self.posts: list[tuple[str, str, str]] = []
        self.result = True
        self.error: Exception | None = None

    async def post(self, room_id: str, text: str, *, client_msg_id: str) -> bool:
        import asyncio
        await asyncio.sleep(0)
        if self.error is not None:
            raise self.error
        self.posts.append((room_id, text, client_msg_id))
        return self.result

    def texts(self, room_id: str | None = None) -> list[str]:
        return [t for r, t, _ in self.posts if room_id is None or r == room_id]
