"""채팅 프레임 해석 (Phase 5 설계 4절, FR-C05·C06). 순수 함수이고 어떤 입력도 예외를 내지 않는다.

알 수 없는 type은 UnknownFrame, 필수 필드가 없거나 형식이 틀리면 BadFrame으로 돌려준다. 모르는 필드는 무시한다.
BadFrame.reason에는 필드 이름만 쓴다(값·본문을 남기지 않는다).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .models import (AckFrame, BadFrame, ChatMessage, ErrorFrame, Frame, HelloFrame, MessageFrame, RoomFrame, UnknownFrame)

_MESSAGE_STR_FIELDS = (("roomId", "room_id"), ("senderId", "sender_id"), ("body", "body"), ("kind", "kind"), ("createdAt", "created_at"))


def parse_frame(raw: str | bytes | dict[str, Any]) -> Frame:
    data = raw if isinstance(raw, dict) else _load(raw)
    if isinstance(data, BadFrame):
        return data
    kind = data.get("type")
    if not isinstance(kind, str) or not kind:
        return BadFrame("type")
    handler = _HANDLERS.get(kind)
    if handler is None:
        return UnknownFrame(kind)
    return handler(data)


def parse_message(data: dict[str, Any]) -> ChatMessage | BadFrame:
    """REST 메시지(type 없음)와 WS message 프레임이 같은 모양이다."""
    if not isinstance(data, dict):
        return BadFrame("message")
    values: dict[str, Any] = {}
    for wire, attr in _MESSAGE_STR_FIELDS:
        v = data.get(wire)
        if not isinstance(v, str):
            return BadFrame(wire)
        values[attr] = v
    seq = data.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int):
        return BadFrame("seq")
    return ChatMessage(seq=seq, **values)


def _load(raw: str | bytes) -> dict[str, Any] | BadFrame:
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        data = json.loads(text)
    except (UnicodeDecodeError, ValueError):
        return BadFrame("json")
    return data if isinstance(data, dict) else BadFrame("json")


def _message(d: dict[str, Any]) -> Frame:
    m = parse_message(d)
    return m if isinstance(m, BadFrame) else MessageFrame(m)


def _room(d: dict[str, Any]) -> Frame:
    room_id, change = d.get("roomId"), d.get("change")
    if not isinstance(room_id, str):
        return BadFrame("roomId")
    if not isinstance(change, str):
        return BadFrame("change")
    return RoomFrame(room_id, change)


def _hello(d: dict[str, Any]) -> Frame:
    user_id, room_ids = d.get("userId"), d.get("roomIds")
    if not isinstance(user_id, str) or not user_id:
        return BadFrame("userId")
    if not isinstance(room_ids, list) or not all(isinstance(r, str) for r in room_ids):
        return BadFrame("roomIds")
    return HelloFrame(user_id, tuple(room_ids))


def _ack(d: dict[str, Any]) -> Frame:
    return AckFrame({k: v for k, v in d.items() if k != "type"})


def _error(d: dict[str, Any]) -> Frame:
    def text(key: str) -> str | None:
        v = d.get(key)
        return v if isinstance(v, str) else None

    details = d.get("details")
    return ErrorFrame(code=text("code"), message=text("message"), details=dict(details) if isinstance(details, dict) else {},
                      room_id=text("roomId"), client_msg_id=text("clientMsgId"))


_HANDLERS: dict[str, Callable[[dict[str, Any]], Frame]] = {
    "message": _message, "room": _room, "hello": _hello, "ack": _ack, "error": _error,
}
