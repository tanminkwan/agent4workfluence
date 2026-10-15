"""ChatSender: 방에 글 보내기 (Phase 5 설계 5.2절, FR-C21·C22·C24·C25·C27). ReplySender 구현.

평문화 → 길이 규칙 → 속도 제한 → 보내기 순서다. 같은 clientMsgId로 다시 보내도 서버가 한 번만 반영하므로 429·5xx·네트워크 오류는
재시도하고, 그 밖의 4xx는 재시도하지 않는다. 글 본문은 로그에 남기지 않는다(NFR-C03).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Protocol

from .ports import ChatApi, ChatApiError, ReplySender
from .textfmt import fit_answer, to_plain_text

log = logging.getLogger(__name__)
MAX_CLIENT_MSG_ID = 64
_MARKUP_ONLY = re.compile(r"^[\s*_~#>`|\-]*$")


class Limiter(Protocol):
    async def acquire(self) -> None: ...


def _short_id(client_msg_id: str) -> str:
    """서버는 clientMsgId를 1~64자로 받는다. 길면 결정적으로 줄여 멱등을 지킨다."""
    if len(client_msg_id) <= MAX_CLIENT_MSG_ID:
        return client_msg_id
    return f"{client_msg_id[:40]}~{hashlib.sha256(client_msg_id.encode()).hexdigest()[:20]}"


class ChatSender(ReplySender):
    def __init__(self, api: ChatApi, *, limiter: Limiter, answer_max_chars: int, hard_max_chars: int, truncate_notice: str,
                 retry_max: int, retry_seconds: float, retry_wait_max_seconds: float,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        fit_answer("", max_chars=answer_max_chars, hard_max_chars=hard_max_chars, notice=truncate_notice)   # 설정이 틀리면 바로 드러낸다
        if retry_max < 0 or retry_seconds < 0 or retry_wait_max_seconds <= 0:
            raise ValueError("보내기 재시도 설정이 맞지 않는다")
        self._api, self._limiter, self._sleep = api, limiter, sleep
        self._answer_max, self._hard_max, self._notice = answer_max_chars, hard_max_chars, truncate_notice
        self._retry_max, self._retry_seconds, self._wait_max = retry_max, retry_seconds, retry_wait_max_seconds

    async def post(self, room_id: str, text: str, *, client_msg_id: str) -> bool:
        plain = to_plain_text(text)
        if _MARKUP_ONLY.match(plain):
            log.warning("chat post skipped: empty text room=%s", room_id[:8])
            return False
        body = fit_answer(plain, max_chars=self._answer_max, hard_max_chars=self._hard_max, notice=self._notice)
        cid = _short_id(client_msg_id)
        for attempt in range(self._retry_max + 1):
            await self._limiter.acquire()
            try:
                await self._api.send(room_id, body.text, client_msg_id=cid)
                return True
            except ChatApiError as e:
                if self._retryable(e) and attempt < self._retry_max:
                    wait = min(e.retry_after_seconds if e.retry_after_seconds is not None else self._retry_seconds, self._wait_max)
                    log.info("chat send retry: room=%s code=%s attempt=%d wait=%.1fs", room_id[:8], e.code, attempt + 1, wait)
                    await self._sleep(wait)
                    continue
                log.warning("chat send failed: room=%s code=%s status=%s attempts=%d", room_id[:8], e.code, e.status, attempt + 1)
                return False
        return False

    @staticmethod
    def _retryable(e: ChatApiError) -> bool:
        return e.status is None or e.status >= 500 or e.status == 429 or e.code == "RATE_LIMITED"
