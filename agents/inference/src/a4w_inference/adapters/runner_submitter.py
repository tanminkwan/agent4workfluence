"""채팅 채널의 RequestSubmitter를 Phase 4 RequestRunner에 잇는다 (Phase 5 설계 5.1절).

채팅 채널은 runner를 모르고 ChatSubmit·ChatOutcome·SubmitRejected만 안다. runner의 거절 코드는 그대로 넘긴다.
"""

from __future__ import annotations

from ..channels.chat.ports import ChatOutcome, ChatSubmit, RequestSubmitter, SubmitRejected
from ..runner import RequestRunner, SubmitError, SubmitRequest


class RunnerSubmitter(RequestSubmitter):
    def __init__(self, runner: RequestRunner):
        self._runner = runner

    async def submit(self, args: ChatSubmit) -> str:
        try:
            accepted = await self._runner.submit(SubmitRequest(
                agent_id=args.agent_id, text=args.text, requester=args.requester, conversation_id=args.conversation_id,
                idempotency_key=args.idempotency_key, allow_writes=args.allow_writes, channel_notes=args.channel_notes))
        except SubmitError as e:
            raise SubmitRejected(e.code, e.message) from None
        return accepted.request_id

    async def get(self, request_id: str) -> ChatOutcome | None:
        record = self._runner.get(request_id)
        if record is None:
            return None
        return ChatOutcome(status=record.status, complete=record.complete, stop_reason=record.stop_reason, answer=record.answer,
                           error_code=record.error_code)
