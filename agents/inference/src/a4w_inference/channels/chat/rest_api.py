"""WorkfluenceChatApi: Workfluence 채팅 REST 호출 (Phase 5 설계 2절, D7). 채팅 REST는 tool이 아니라 이 어댑터 코드가 부른다.

응답 모양은 공개 openapi.yaml(chat.rooms·chat.room·chat.messages)과 workf 확인(2026-10-15) 기준이다. token은 오류·repr에 남기지 않는다.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import httpx

from .frames import parse_message
from .models import BadFrame, ChatMessage, Member, RoomInfo
from .ports import ChatApi, ChatApiError, SendResult

log = logging.getLogger(__name__)
MAX_CLIENT_MSG_ID = 64


class WorkfluenceChatApi(ChatApi):
    def __init__(self, base_url: str, token: str, *, client: httpx.AsyncClient | None = None, timeout: float = 15.0,
                 ca_file: str | None = None):
        self._base = base_url.rstrip("/")
        self._token = token
        self._client = client or httpx.AsyncClient(timeout=timeout, verify=ca_file or True)
        self.bad_items = 0

    def __repr__(self) -> str:
        return f"WorkfluenceChatApi(base={self._base!r})"

    async def rooms(self) -> list[RoomInfo]:
        body = await self._get("/chat/rooms")
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise ChatApiError("BAD_RESPONSE", "방 목록 응답의 모양이 다르다")
        out: list[RoomInfo] = []
        for item in items:
            room = _room(item)
            if room is None:
                self.bad_items += 1
            else:
                out.append(room)
        return out

    async def room(self, room_id: str) -> RoomInfo:
        room = _room(await self._get(f"/chat/rooms/{quote(room_id, safe='')}"))
        if room is None:
            raise ChatApiError("BAD_RESPONSE", "방 응답의 모양이 다르다")
        return room

    async def messages(self, room_id: str, *, after_seq: int, limit: int) -> list[ChatMessage]:
        body = await self._get(f"/chat/rooms/{quote(room_id, safe='')}/messages", {"afterSeq": after_seq, "limit": limit})
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise ChatApiError("BAD_RESPONSE", "메시지 목록 응답의 모양이 다르다")
        out: list[ChatMessage] = []
        for item in items:
            m = parse_message(item) if isinstance(item, dict) else BadFrame("message")
            if isinstance(m, BadFrame):
                self.bad_items += 1
                continue
            out.append(m)
        return out

    async def send(self, room_id: str, body: str, *, client_msg_id: str | None = None) -> SendResult:
        if client_msg_id is not None and not 1 <= len(client_msg_id) <= MAX_CLIENT_MSG_ID:
            raise ValueError("clientMsgId는 1~64자여야 한다")
        payload: dict[str, Any] = {"body": body}
        if client_msg_id is not None:
            payload["clientMsgId"] = client_msg_id
        response = await self._call("POST", f"/chat/rooms/{quote(room_id, safe='')}/messages", json=payload)
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):                     # 성공 상태면 보낸 것이다. 본문이 빈약해도 실패로 보지 않는다
            return SendResult(message_id=None, duplicate=False)
        message_id = data.get("id")
        return SendResult(message_id=message_id if isinstance(message_id, str) else None, duplicate=data.get("duplicate") is True)

    # ------------------------------------------------------------------

    async def _call(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None) -> httpx.Response:
        try:
            response = await self._client.request(method, self._base + path, params=params, json=json,
                                                  headers={"Authorization": f"Bearer {self._token}"})
        except httpx.HTTPError as e:
            raise ChatApiError("NETWORK", f"채팅 서버에 닿지 못했다: {type(e).__name__}") from None
        if response.status_code >= 400:
            raise _error(response)
        return response

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = await self._call("GET", path, params=params)
        try:
            return response.json()
        except ValueError:
            raise ChatApiError("BAD_RESPONSE", "JSON이 아닌 응답이다", status=response.status_code) from None


def _error(response: httpx.Response) -> ChatApiError:
    code, message, request_id, retry = f"HTTP_{response.status_code}", "채팅 API 오류", None, None
    try:
        err = response.json().get("error")
    except (ValueError, AttributeError):
        err = None
    if isinstance(err, dict):
        code = err.get("code") if isinstance(err.get("code"), str) else code
        message = err.get("message") if isinstance(err.get("message"), str) else message
        request_id = err.get("requestId") if isinstance(err.get("requestId"), str) else None
        details = err.get("details")
        if isinstance(details, dict) and isinstance(details.get("retryAfterSec"), (int, float)):
            retry = float(details["retryAfterSec"])
    if retry is None:
        try:
            retry = float(response.headers["Retry-After"])
        except (KeyError, ValueError):
            retry = None
    return ChatApiError(code, message, status=response.status_code, request_id=request_id, retry_after_seconds=retry)


def _room(item: Any) -> RoomInfo | None:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("kind"), str):
        return None
    members, past = _members(item.get("members")), _members(item.get("pastMembers"))
    last = item.get("lastSeq")
    if isinstance(last, bool) or not isinstance(last, int):
        lm = item.get("lastMessage")
        last = lm.get("seq") if isinstance(lm, dict) and isinstance(lm.get("seq"), int) and not isinstance(lm.get("seq"), bool) else 0
    return RoomInfo(room_id=item["id"], kind=item["kind"], name=item.get("name") or "", members=members, past_members=past, last_seq=last)


def _members(value: Any) -> tuple[Member, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(Member(m["userId"], m.get("displayName") or "", m["username"] if isinstance(m.get("username"), str) else None)
                 for m in value if isinstance(m, dict) and isinstance(m.get("userId"), str))
