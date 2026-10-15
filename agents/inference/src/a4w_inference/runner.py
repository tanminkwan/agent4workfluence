"""RequestRunner: 접수·비동기 수행·멱등·동시 수·대화 직렬화·재시작 복구 (Phase 4 설계 5.4). 채널(REST·채팅)이 공통으로 쓴다.

채널·HTTP·DB 구현을 모른다. 접수 검사와 행 추가 사이에 await가 없어(단일 event loop) 같은 대화·같은 key의 경쟁이 생기지 않는다.
worker는 1개로 고정한다(설계 13.1). 요청 text·답·tool 인자는 로그에 남기지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .engine.models import RunFailed, TurnRequest
from .engine.ports import TurnEngine
from .records import RequestRecord, RequestStore

log = logging.getLogger(__name__)
ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class SubmitError(Exception):
    code = "ERROR"
    http_status = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ValidationFailed(SubmitError):
    code, http_status = "VALIDATION_ERROR", 400


class AgentNotFound(SubmitError):
    code, http_status = "AGENT_NOT_FOUND", 404


class IdempotencyConflict(SubmitError):
    code, http_status = "IDEMPOTENCY_CONFLICT", 409


class ConversationBusy(SubmitError):
    code, http_status = "CONVERSATION_BUSY", 409


class AgentBusy(SubmitError):
    code, http_status = "AGENT_BUSY", 429


@dataclass(frozen=True)
class SubmitRequest:
    agent_id: str
    text: str
    requester: str
    conversation_id: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True)
class Accepted:
    request_id: str
    status: str
    conversation_id: str | None
    replayed: bool = False


class RequestRunner:
    def __init__(self, *, engine: TurnEngine, db: RequestStore, agent_limits: Mapping[str, int], text_max_chars: int,
                 requester_max_chars: int, now_iso: Callable[[], str], new_id: Callable[[], str]):
        self._engine, self._db, self._limits = engine, db, dict(agent_limits)
        self._text_max, self._requester_max = text_max_chars, requester_max_chars
        self._now, self._new_id = now_iso, new_id
        self._running = {agent: 0 for agent in self._limits}
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ 접수

    async def submit(self, req: SubmitRequest) -> Accepted:
        self._validate(req)
        if req.agent_id not in self._limits:
            raise AgentNotFound(f"등록되지 않은 agent다: {req.agent_id}")
        if req.idempotency_key:
            existing = self._db.find_by_key(req.agent_id, req.idempotency_key)
            if existing is not None:
                if existing.text != req.text:
                    raise IdempotencyConflict("같은 idempotencyKey에 다른 text가 왔다")
                return Accepted(existing.request_id, existing.status, existing.conversation_id, replayed=True)
        if req.conversation_id and self._db.running_in_conversation(req.agent_id, req.conversation_id):
            raise ConversationBusy("같은 대화의 요청이 아직 진행 중이다")
        if self._running[req.agent_id] >= self._limits[req.agent_id]:
            raise AgentBusy("agent가 동시에 처리할 수 있는 요청 수를 넘었다")
        request_id = self._new_id()
        self._db.create_running(request_id=request_id, agent_id=req.agent_id, requester=req.requester, text=req.text,
                                conversation_id=req.conversation_id, idempotency_key=req.idempotency_key, created_at=self._now())
        self._running[req.agent_id] += 1
        task = asyncio.create_task(self._execute(request_id, req))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return Accepted(request_id, "running", req.conversation_id)

    def _validate(self, req: SubmitRequest) -> None:
        if not req.text or not req.text.strip():
            raise ValidationFailed("text가 비었다")
        if len(req.text) > self._text_max:
            raise ValidationFailed(f"text가 {self._text_max}자를 넘는다")
        if not req.requester or not req.requester.strip():
            raise ValidationFailed("requester가 비었다")
        if len(req.requester) > self._requester_max:
            raise ValidationFailed(f"requester가 {self._requester_max}자를 넘는다")
        for name, value in (("conversationId", req.conversation_id), ("idempotencyKey", req.idempotency_key)):
            if value is not None and not ID_PATTERN.match(value):
                raise ValidationFailed(f"{name}는 영문자·숫자·._:- 만 쓰고 128자 이하여야 한다")

    # ------------------------------------------------------------------ 수행

    async def _execute(self, request_id: str, req: SubmitRequest) -> None:
        started = time.monotonic()
        turn = TurnRequest(agent_id=req.agent_id, text=req.text, requester=req.requester, conversation_id=req.conversation_id)
        status, extra = "failed", ""
        try:
            try:
                result = await self._engine.run(turn)
            except RunFailed as e:
                self._db.finish_failed(request_id, code=e.code, message=e.message, actions=e.actions, usage=e.usage,
                                       finished_at=self._now(), elapsed_ms=self._elapsed(started))
                extra = f"code={e.code} steps={e.usage.steps} writes={e.usage.write_calls}"
            except asyncio.CancelledError:
                self._db.finish_failed(request_id, code="INTERRUPTED", message="수행이 취소되었다", actions=(), usage=_empty_usage(),
                                       finished_at=self._now(), elapsed_ms=self._elapsed(started))
                raise
            except Exception as e:      # 내용(요청 text·token)이 들어 있을 수 있어 종류만 남긴다
                log.error("request=%s unexpected error: %s", request_id, type(e).__name__)
                self._db.finish_failed(request_id, code="INTERNAL", message="예상하지 못한 오류가 났다", actions=(),
                                       usage=_empty_usage(), finished_at=self._now(), elapsed_ms=self._elapsed(started))
                extra = "code=INTERNAL"
            else:
                self._db.finish_done(request_id, result, finished_at=self._now(), elapsed_ms=self._elapsed(started))
                status = "done"
                u = result.usage
                extra = (f"complete={result.complete} stop={result.stop_reason} steps={u.steps} tools={u.tool_calls} "
                         f"writes={u.write_calls} promptTokens={u.prompt_tokens} lastInput={u.last_input_tokens} "
                         f"dropped={u.history_dropped_turns}")
        finally:
            self._running[req.agent_id] -= 1
        log.info("request=%s agent=%s conv=%s status=%s %s elapsedMs=%d", request_id, req.agent_id,
                 "yes" if req.conversation_id else "no", status, extra, self._elapsed(started))

    @staticmethod
    def _elapsed(started: float) -> int:
        return int((time.monotonic() - started) * 1000)

    # ------------------------------------------------------------------ 조회·복구

    def get(self, request_id: str) -> RequestRecord | None:
        return self._db.get(request_id)

    def recover(self) -> int:
        """재시작으로 끊긴 running 행을 INTERRUPTED로 마감한다. 다시 수행하지 않는다(쓰기가 겹칠 수 있다)."""
        n = self._db.mark_interrupted(finished_at=self._now())
        if n:
            log.warning("interrupted requests closed: %d", n)
        return n

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


def _empty_usage():
    from .engine.models import Usage
    return Usage()
