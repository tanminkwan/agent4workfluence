"""채팅 채널이 주고받는 값 (Phase 5 설계 2·4절). Workfluence 채팅 프레임 모양을 담는다(타겟 종속 어댑터)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

KIND_USER = "user"


@dataclass(frozen=True)
class ChatMessage:
    room_id: str
    seq: int
    sender_id: str
    body: str
    kind: str
    created_at: str

    @property
    def is_user(self) -> bool:
        """요청 후보인가. join·leave·알 수 없는 kind는 아니다 (FR-C05)."""
        return self.kind == KIND_USER


@dataclass(frozen=True)
class Member:
    user_id: str
    display_name: str


@dataclass(frozen=True)
class RoomInfo:
    room_id: str
    kind: str                          # direct | group (그 밖의 값은 알 수 없는 방으로 본다)
    name: str
    members: tuple[Member, ...] = ()
    past_members: tuple[Member, ...] = ()   # 방을 나간 사람(나간 사람이 쓴 메시지의 이름 찾기용)
    last_seq: int = 0                  # 방의 마지막 메시지 번호 (처음 보는 방의 시작점)


@dataclass(frozen=True)
class MessageFrame:
    message: ChatMessage


@dataclass(frozen=True)
class RoomFrame:
    room_id: str
    change: str


@dataclass(frozen=True)
class HelloFrame:
    user_id: str                       # 이 연결 주인(=내 id)
    room_ids: tuple[str, ...]          # 연결 시점에 든 방


@dataclass(frozen=True)
class AckFrame:
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ErrorFrame:
    code: str | None = None
    message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    room_id: str | None = None
    client_msg_id: str | None = None


@dataclass(frozen=True)
class UnknownFrame:
    type: str


@dataclass(frozen=True)
class BadFrame:
    """필수 필드가 없거나 깨진 프레임. reason에는 필드 이름만 담는다(본문·값이 로그로 새지 않게, NFR-C03)."""
    reason: str


Frame = MessageFrame | RoomFrame | HelloFrame | AckFrame | ErrorFrame | UnknownFrame | BadFrame


@dataclass(frozen=True)
class IncomingRequest:
    """dispatcher가 수행 연결(RoomWorker)에 넘기는 요청 (설계 3절)."""
    room: RoomInfo
    message: ChatMessage
    requester: str
    allow_writes: bool
