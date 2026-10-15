"""채팅 채널의 interface (Phase 5 설계 2절, D7). 구현은 타겟 어댑터(rest_api 등)에 있다."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .models import ChatMessage, RoomInfo


class ChatApiError(Exception):
    """채팅 API 호출 실패. status가 없으면 네트워크 문제다. token·본문은 담지 않는다."""

    def __init__(self, code: str, message: str, *, status: int | None = None, request_id: str | None = None,
                 retry_after_seconds: float | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status
        self.request_id = request_id
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class SendResult:
    message_id: str | None
    duplicate: bool = False


class ChatApi(ABC):
    @abstractmethod
    async def rooms(self) -> list[RoomInfo]: ...

    @abstractmethod
    async def room(self, room_id: str) -> RoomInfo: ...

    @abstractmethod
    async def messages(self, room_id: str, *, after_seq: int, limit: int) -> list[ChatMessage]:
        """after_seq 이후 메시지를 seq 오름차순으로 limit개까지."""

    @abstractmethod
    async def send(self, room_id: str, body: str, *, client_msg_id: str | None = None) -> SendResult: ...


# ── 수행 연결 (Phase 5 설계 5.1절): 채팅 채널은 수행 로직(runner)을 이 좁은 interface로만 안다 ───────────────────

@dataclass(frozen=True)
class ChatSubmit:
    agent_id: str
    text: str
    requester: str
    conversation_id: str
    idempotency_key: str
    allow_writes: bool = False
    channel_notes: str | None = None


@dataclass(frozen=True)
class ChatOutcome:
    status: str                         # running | done | failed
    complete: bool | None
    stop_reason: str | None
    answer: str | None
    error_code: str | None


class SubmitRejected(Exception):
    """수행 로직이 접수를 거절했다. code: AGENT_BUSY·CONVERSATION_BUSY(기다렸다 다시)·VALIDATION_ERROR·그 밖."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class RequestSubmitter(ABC):
    @abstractmethod
    async def submit(self, args: ChatSubmit) -> str:
        """접수하고 request_id를 돌려준다. 거절은 SubmitRejected."""

    @abstractmethod
    async def get(self, request_id: str) -> ChatOutcome | None:
        """진행·결과. 기록이 없으면 None."""


class ReplySender(ABC):
    @abstractmethod
    async def post(self, room_id: str, text: str, *, client_msg_id: str) -> bool:
        """방에 글을 보낸다(평문화·길이 규칙·속도 제한·재시도는 구현이 맡는다). 끝내 못 보내면 False."""
