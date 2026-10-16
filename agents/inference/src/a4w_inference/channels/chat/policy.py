"""ReplyPolicy: 어떤 메시지가 요청인가, 쓰기를 허용하는가 (Phase 5 설계 4.2·8절, FR-C05·C11·C12·C32·C33).

순수 판단이다. 입력은 값 객체뿐이고 네트워크·저장소를 모른다. 쓰기 허용은 senderId(서버가 채운 값)로만 판단한다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from .models import ChatMessage, RoomInfo

ROOM_DIRECT = "direct"
ROOM_GROUP = "group"
AFTER_MENTION = r"[\s,.!?:;~]*"          # 호명 바로 뒤의 문장 부호·공백
BEFORE_MENTION = "@?"                     # 채팅 멘션(@이름)의 @ (설계 19절)


@dataclass(frozen=True)
class Decision:
    is_request: bool
    reason: str
    allow_writes: bool = False


def _normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


class ReplyPolicy:
    def __init__(self, *, mention_name: str, write_allowed_users: Iterable[str]):
        self._mention = _normalize(mention_name)
        if not self._mention:
            raise ValueError("CHAT_MENTION_NAME이 비었다")
        self._writers = frozenset(write_allowed_users)

    def decide(self, message: ChatMessage, room: RoomInfo | None, *, my_user_id: str) -> Decision:
        if not message.is_user:
            return Decision(False, "not_user_kind")
        if message.sender_id == my_user_id:
            return Decision(False, "own_message")
        if room is None:
            return Decision(False, "unknown_room")
        if room.kind == ROOM_DIRECT:
            return self._request("direct", message)
        if room.kind == ROOM_GROUP:
            body = _normalize(message.body)
            names = (self._mention, *(_normalize(n) for n in my_names(room, my_user_id)))
            if any(n and n in body for n in names):
                return self._request("mentioned", message)
            return Decision(False, "not_mentioned")
        return Decision(False, "unknown_room_kind")

    def _request(self, reason: str, message: ChatMessage) -> Decision:
        return Decision(True, reason, allow_writes=message.sender_id in self._writers)


def my_names(room: RoomInfo | None, my_user_id: str | None) -> tuple[str, ...]:
    """방에서 내 표시 이름. 채팅 화면의 멘션은 "@<members[].displayName>"이다 (workf F-024, 설계 19절)."""
    if room is None or not my_user_id:
        return ()
    return tuple(m.display_name for m in room.members if m.user_id == my_user_id and m.display_name.strip())


def requester_label(sender_id: str, room: RoomInfo | None) -> str:
    """요청자 이름: 지금 멤버 → 나간 사람(pastMembers) 순으로 표시 이름을 찾고, 없으면 senderId."""
    if room is not None:
        for member in (*room.members, *room.past_members):
            if member.user_id == sender_id and member.display_name.strip():
                return member.display_name
    return sender_id


def strip_mention(body: str, mention_name: str) -> str:
    """요청 글에서 호명(대소문자·공백 차이 무시)과 바로 뒤 문장 부호를 뺀다. 이름만 있는 글이면 원문을 둔다 (설계 20절)."""
    words = mention_name.split()
    if not words:
        return body.strip()
    pattern = re.compile(BEFORE_MENTION + r"\s+".join(re.escape(w) for w in words) + AFTER_MENTION, re.IGNORECASE)
    stripped = pattern.sub("", body).strip()
    return stripped or body.strip()
