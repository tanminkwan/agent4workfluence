"""WorkfluenceChatApi: 채팅 REST 호출 (Phase 5 설계 2·4.3절, D7). httpx MockTransport로 확인한다.

응답 모양은 openapi.yaml(chat.rooms·chat.room·chat.messages)과 workf 확인 기준이다. 보내기(send)는 S6에서 다룬다.
"""

from __future__ import annotations

import httpx
import pytest

from a4w_inference.channels.chat.models import ChatMessage, Member, RoomInfo
from a4w_inference.channels.chat.ports import ChatApiError
from a4w_inference.channels.chat.rest_api import WorkfluenceChatApi
from tests.chat_fakes import ALICE, BOB, ME, ROOM, wire_message

pytestmark = pytest.mark.anyio
BASE = "https://wf.test/api/v1"
TOKEN = "tok-secret-123"


def api_with(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WorkfluenceChatApi(BASE, TOKEN, client=client), client


def room_json(room_id=ROOM, kind="group", last_seq=5, name="시험 방"):
    return {"id": room_id, "kind": kind, "name": name, "createdBy": ALICE, "lastSeq": last_seq, "unread": 0,
            "members": [{"userId": ME, "displayName": "AI Agent"}, {"userId": ALICE, "displayName": "tiffanie"}],
            "lastMessage": {"id": "m1", "roomId": room_id, "seq": last_seq, "senderId": ALICE, "body": "hi", "kind": "user",
                            "createdAt": "2026-10-15T06:14:07.882Z"}}


async def test_A1_rooms_lists_my_rooms_with_members_and_the_last_seq():
    seen = {}

    def handler(request):
        seen["url"], seen["auth"] = str(request.url), request.headers.get("Authorization")
        return httpx.Response(200, json={"items": [room_json(), room_json("r-2", kind="direct", last_seq=0, name=None)]})

    api, _ = api_with(handler)
    rooms = await api.rooms()
    assert seen["url"] == f"{BASE}/chat/rooms" and seen["auth"] == f"Bearer {TOKEN}"
    assert rooms[0] == RoomInfo(room_id=ROOM, kind="group", name="시험 방", last_seq=5,
                                members=(Member(ME, "AI Agent"), Member(ALICE, "tiffanie")))
    assert rooms[1].room_id == "r-2" and rooms[1].kind == "direct" and rooms[1].name == ""


async def test_A2_last_seq_falls_back_to_the_last_message_seq_then_zero():
    no_last_seq = room_json(last_seq=8)
    del no_last_seq["lastSeq"]
    empty = room_json("r-3", last_seq=0)
    del empty["lastSeq"], empty["lastMessage"]
    empty["lastMessage"] = None
    api, _ = api_with(lambda r: httpx.Response(200, json={"items": [no_last_seq, empty]}))
    a, b = await api.rooms()
    assert a.last_seq == 8 and b.last_seq == 0


async def test_A3_room_reads_one_room():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=room_json())

    api, _ = api_with(handler)
    room = await api.room(ROOM)
    assert seen["url"] == f"{BASE}/chat/rooms/{ROOM}" and room.room_id == ROOM and room.members[1].display_name == "tiffanie"


async def test_A4_messages_uses_after_seq_and_limit_and_keeps_the_server_order():
    seen = {}

    def handler(request):
        seen["params"] = dict(request.url.params)
        seen["path"] = request.url.path
        return httpx.Response(200, json={"items": [wire_message(4, frame=False), wire_message(5, "AI Agent 안녕", sender=BOB, frame=False)]})

    api, _ = api_with(handler)
    got = await api.messages(ROOM, after_seq=3, limit=200)
    assert seen["path"] == f"/api/v1/chat/rooms/{ROOM}/messages" and seen["params"] == {"afterSeq": "3", "limit": "200"}
    assert [m.seq for m in got] == [4, 5] and isinstance(got[0], ChatMessage) and got[1].sender_id == BOB and got[1].kind == "user"


async def test_A5_malformed_message_items_are_dropped_and_counted_not_raised():
    good = wire_message(2, frame=False)
    bad = wire_message(3, frame=False)
    del bad["kind"]
    api, _ = api_with(lambda r: httpx.Response(200, json={"items": [good, bad, "junk", 7]}))
    got = await api.messages(ROOM, after_seq=1, limit=200)
    assert [m.seq for m in got] == [2] and api.bad_items == 3


async def test_A6_an_unexpected_body_shape_is_an_api_error():
    api, _ = api_with(lambda r: httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(ChatApiError) as e:
        await api.rooms()
    assert e.value.code == "BAD_RESPONSE"
    api2, _ = api_with(lambda r: httpx.Response(200, content=b"<html>not json</html>"))
    with pytest.raises(ChatApiError) as e2:
        await api2.room(ROOM)
    assert e2.value.code == "BAD_RESPONSE"


@pytest.mark.parametrize("status,code", [(401, "UNAUTHORIZED"), (403, "FORBIDDEN"), (404, "NOT_FOUND"), (429, "RATE_LIMITED"), (500, "INTERNAL")])
async def test_A7_http_errors_become_chat_api_errors_with_the_server_code(status, code):
    body = {"error": {"code": code, "message": "서버 메시지", "requestId": "req-1"}}
    api, _ = api_with(lambda r: httpx.Response(status, json=body))
    with pytest.raises(ChatApiError) as e:
        await api.rooms()
    err = e.value
    assert err.status == status and err.code == code and err.request_id == "req-1"


async def test_A8_retry_after_is_taken_from_error_details_or_the_header():
    body = {"error": {"code": "RATE_LIMITED", "message": "느리게", "requestId": "r", "details": {"retryAfterSec": 12}}}
    api, _ = api_with(lambda r: httpx.Response(429, json=body))
    with pytest.raises(ChatApiError) as e:
        await api.rooms()
    assert e.value.retry_after_seconds == 12
    api2, _ = api_with(lambda r: httpx.Response(429, json={"error": {"code": "RATE_LIMITED", "message": "m"}}, headers={"Retry-After": "7"}))
    with pytest.raises(ChatApiError) as e2:
        await api2.rooms()
    assert e2.value.retry_after_seconds == 7


async def test_A9_a_non_json_error_body_still_gives_a_status_based_error():
    api, _ = api_with(lambda r: httpx.Response(502, content=b"<html>bad gateway</html>"))
    with pytest.raises(ChatApiError) as e:
        await api.rooms()
    assert e.value.status == 502 and e.value.code == "HTTP_502"


async def test_A10_network_failures_become_chat_api_errors_without_a_status():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    api, _ = api_with(handler)
    with pytest.raises(ChatApiError) as e:
        await api.messages(ROOM, after_seq=0, limit=200)
    assert e.value.status is None and e.value.code == "NETWORK"

    def slow(request):
        raise httpx.ReadTimeout("slow")

    api2, _ = api_with(slow)
    with pytest.raises(ChatApiError) as e2:
        await api2.room(ROOM)
    assert e2.value.code == "NETWORK"


async def test_A11_the_token_is_never_in_errors_or_repr():
    api, _ = api_with(lambda r: httpx.Response(401, json={"error": {"code": "UNAUTHORIZED", "message": "no", "requestId": "r"}}))
    with pytest.raises(ChatApiError) as e:
        await api.rooms()
    assert TOKEN not in str(e.value) and TOKEN not in repr(e.value) and TOKEN not in repr(api) and TOKEN not in str(api)


async def test_A12_a_base_url_with_a_trailing_slash_is_accepted():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"items": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = WorkfluenceChatApi(BASE + "/", TOKEN, client=client)
    assert await api.rooms() == [] and seen["url"] == f"{BASE}/chat/rooms"


async def test_A13_room_ids_are_percent_encoded_in_the_path():
    seen = {}

    def handler(request):
        seen["raw"] = request.url.raw_path.decode()
        return httpx.Response(200, json=room_json())

    api, _ = api_with(handler)
    await api.room("a/b c")
    assert "/chat/rooms/a%2Fb%20c" in seen["raw"]
