"""채팅 채널 시험용 가짜들 (Phase 5 설계 13.2). 실제 서버의 프레임 모양은 workf 확인(2026-10-15)과 openapi.yaml 기준이다."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Any

from a4w_inference.channels.chat.models import ChatMessage, Member, RoomInfo
from a4w_inference.channels.chat.ports import ChatApi, ChatApiError, SendResult

ME = "c4838c0e-f59b-42ab-9381-6ca531d29e14"
ALICE = "ba6387f9-6d24-4a9f-8dc5-b57b57f1cb38"
BOB = "d15736fd-d217-4f85-9219-bfb6435143ac"
ROOM = "bfe0ce49-02f4-40da-b46c-a9c8f84deb25"
ROOM2 = "0f5f3d52-7a3e-4a70-9a43-6a0d3a6c1111"
NOW_ISO = "2026-10-15T07:00:00Z"
RECENT = "2026-10-15T06:30:00.000Z"
OLD = "2026-10-10T06:30:00.000Z"


def members(*pairs):
    return tuple(Member(u, n) for u, n in pairs)


def room_info(room_id=ROOM, kind="direct", last_seq=0, name="시험 방", member_list=None):
    ms = member_list if member_list is not None else members((ME, "AI Agent"), (ALICE, "tiffanie"), (BOB, "tanminkwan"))
    return RoomInfo(room_id=room_id, kind=kind, name=name, members=ms, last_seq=last_seq)


def chat_message(seq, body="회의록 찾아줘", *, room_id=ROOM, sender=ALICE, kind="user", created_at=RECENT):
    return ChatMessage(room_id=room_id, seq=seq, sender_id=sender, body=body, kind=kind, created_at=created_at)


def wire_message(seq, body="회의록 찾아줘", *, room_id=ROOM, sender=ALICE, kind="user", created_at=RECENT, frame=True):
    d = {"roomId": room_id, "seq": seq, "senderId": sender, "body": body, "kind": kind, "createdAt": created_at}
    return {"type": "message", **d} if frame else {"id": f"m-{room_id}-{seq}", **d}


class FakeChatApi(ChatApi):
    """메모리 채팅 API. messages()는 실제처럼 seq 오름차순으로 limit개까지만 준다."""

    def __init__(self, rooms: Sequence[RoomInfo] = (), messages: Sequence[ChatMessage] = ()):
        self.room_list = {r.room_id: r for r in rooms}
        self.stored: list[ChatMessage] = list(messages)
        self.calls: list[tuple] = []
        self.fail_rooms: ChatApiError | None = None
        self.fail_messages: dict[str, ChatApiError] = {}
        self.sent: list[tuple[str, str, str | None]] = []

    async def rooms(self) -> list[RoomInfo]:
        self.calls.append(("rooms",))
        if self.fail_rooms:
            raise self.fail_rooms
        return list(self.room_list.values())

    async def room(self, room_id: str) -> RoomInfo:
        self.calls.append(("room", room_id))
        if room_id not in self.room_list:
            raise ChatApiError("NOT_FOUND", "방이 없다", status=404)
        return self.room_list[room_id]

    async def messages(self, room_id: str, *, after_seq: int, limit: int) -> list[ChatMessage]:
        self.calls.append(("messages", room_id, after_seq, limit))
        if room_id in self.fail_messages:
            raise self.fail_messages[room_id]
        found = sorted((m for m in self.stored if m.room_id == room_id and m.seq > after_seq), key=lambda m: m.seq)
        return found[:limit]

    async def send(self, room_id: str, body: str, *, client_msg_id: str | None = None) -> SendResult:
        self.sent.append((room_id, body, client_msg_id))
        return SendResult(message_id=f"sent-{len(self.sent)}", duplicate=False)

    def calls_of(self, name: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == name]


class ScriptedConnection:
    """감독(supervisor) 시험용 연결. frames를 차례로 내고 끝에서 end(ConnectionEnded)를 올린다."""

    def __init__(self, frames: Sequence[Any], end: Exception):
        self._frames, self._end = list(frames), end
        self.closed = False

    async def frames(self) -> AsyncIterator[Any]:
        try:
            for f in self._frames:
                yield f
            raise self._end
        finally:
            self.closed = True


def scripted_factory(*sessions: ScriptedConnection) -> Callable[[], ScriptedConnection]:
    queue = list(sessions)
    made: list[ScriptedConnection] = []

    def factory() -> ScriptedConnection:
        if not queue:
            raise AssertionError("시험이 준비한 연결을 다 썼다")
        made.append(queue.pop(0))
        return made[-1]

    factory.made = made  # type: ignore[attr-defined]
    return factory


@asynccontextmanager
async def fake_ws_server(script: Callable[[Any], Any], *, expect_token: str | None = "tok-123", reject: int | None = None):
    """로컬 WebSocket 서버. script(connection)이 프레임을 보내고 닫는다. 연결 요청의 헤더·경로는 seen에 남는다."""
    from websockets.asyncio.server import serve

    seen: list[dict[str, Any]] = []

    def process_request(connection, request):
        seen.append({"path": request.path, "authorization": request.headers.get("Authorization")})
        if reject is not None:
            return connection.respond(HTTPStatus(reject), f"{reject}\n")
        if expect_token is not None and request.headers.get("Authorization") != f"Bearer {expect_token}":
            return connection.respond(HTTPStatus.UNAUTHORIZED, "unauthorized\n")
        return None

    async with serve(script, "127.0.0.1", 0, process_request=process_request) as server:
        port = server.sockets[0].getsockname()[1]
        server.url = f"ws://127.0.0.1:{port}/api/v1/chat/ws"
        server.seen = seen
        yield server
