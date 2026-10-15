"""WorkfluenceChatApi.send: 채팅 보내기 REST (Phase 5 설계 5.2절 3번, FR-C25, AC-C6). httpx MockTransport로 확인한다.

POST /chat/rooms/{id}/messages 본문은 body(필수)와 clientMsgId(선택, 1~64자)뿐이고 모르는 칸은 400이다(openapi.yaml, workf 확인).
"""

from __future__ import annotations

import json

import httpx
import pytest

from a4w_inference.channels.chat.ports import ChatApiError, SendResult
from a4w_inference.channels.chat.rest_api import WorkfluenceChatApi
from tests.chat_fakes import ME, ROOM

pytestmark = pytest.mark.anyio
BASE = "https://wf.test/api/v1"
TOKEN = "tok-secret-123"


def api_with(handler):
    return WorkfluenceChatApi(BASE, TOKEN, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def created(duplicate=False):
    return {"id": "m-1", "roomId": ROOM, "seq": 9, "senderId": ME, "body": "답", "kind": "user", "createdAt": "2026-10-15T07:00:00Z",
            "duplicate": duplicate}


async def test_D1_send_posts_only_body_and_client_msg_id_to_the_room_messages_path():
    seen = {}

    def handler(request):
        seen.update(method=request.method, url=str(request.url), auth=request.headers.get("Authorization"),
                    body=json.loads(request.content), ctype=request.headers.get("Content-Type"))
        return httpx.Response(201, json=created())

    res = await api_with(handler).send(ROOM, "안녕하세요", client_msg_id="reply:r-1")
    assert seen["method"] == "POST" and seen["url"] == f"{BASE}/chat/rooms/{ROOM}/messages" and seen["auth"] == f"Bearer {TOKEN}"
    assert seen["body"] == {"body": "안녕하세요", "clientMsgId": "reply:r-1"} and "application/json" in seen["ctype"]
    assert res == SendResult(message_id="m-1", duplicate=False)


async def test_D2_without_a_client_msg_id_the_field_is_omitted():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json=created())

    await api_with(handler).send(ROOM, "답")
    assert seen["body"] == {"body": "답"}


async def test_D3_a_duplicate_response_is_reported_as_duplicate():
    res = await api_with(lambda r: httpx.Response(201, json=created(duplicate=True))).send(ROOM, "답", client_msg_id="c")
    assert res.duplicate is True and res.message_id == "m-1"


@pytest.mark.parametrize("status,payload", [(201, {}), (200, {"id": "m-2"}), (201, {"unexpected": 1})])
async def test_D4_a_success_status_is_success_even_if_the_body_is_thin(status, payload):
    res = await api_with(lambda r: httpx.Response(status, json=payload)).send(ROOM, "답", client_msg_id="c")
    assert isinstance(res, SendResult) and res.duplicate is False and res.message_id == payload.get("id")


async def test_D5_a_success_with_an_unreadable_body_is_still_success():
    res = await api_with(lambda r: httpx.Response(201, content=b"not json")).send(ROOM, "답", client_msg_id="c")
    assert res == SendResult(message_id=None, duplicate=False)


@pytest.mark.parametrize("status,code", [(400, "TOO_LONG"), (400, "EMPTY"), (400, "BAD_CHAR"), (400, "INVALID_REQUEST"),
                                         (403, "INSUFFICIENT_SCOPE"), (403, "FORBIDDEN"), (404, "NOT_FOUND"), (500, "INTERNAL")])
async def test_D6_error_statuses_become_chat_api_errors_with_the_server_code(status, code):
    body = {"error": {"code": code, "message": "m", "requestId": "req-9"}}
    with pytest.raises(ChatApiError) as e:
        await api_with(lambda r: httpx.Response(status, json=body)).send(ROOM, "답", client_msg_id="c")
    assert e.value.status == status and e.value.code == code and e.value.request_id == "req-9"


async def test_D7_rate_limit_carries_retry_after_seconds():
    body = {"error": {"code": "RATE_LIMITED", "message": "느리게", "requestId": "r", "details": {"retryAfterSec": 8}}}
    with pytest.raises(ChatApiError) as e:
        await api_with(lambda r: httpx.Response(429, json=body)).send(ROOM, "답", client_msg_id="c")
    assert e.value.code == "RATE_LIMITED" and e.value.retry_after_seconds == 8


async def test_D8_network_failure_is_a_chat_api_error_without_status():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(ChatApiError) as e:
        await api_with(handler).send(ROOM, "답", client_msg_id="c")
    assert e.value.status is None and e.value.code == "NETWORK"


@pytest.mark.parametrize("bad", ["", "x" * 65])
async def test_D9_a_client_msg_id_outside_1_to_64_chars_is_a_programming_error(bad):
    called = []
    api = api_with(lambda r: called.append(r) or httpx.Response(201, json=created()))
    with pytest.raises(ValueError):
        await api.send(ROOM, "답", client_msg_id=bad)
    assert called == []


async def test_D10_the_body_is_sent_as_given_and_the_token_never_appears_in_errors():
    with pytest.raises(ChatApiError) as e:
        await api_with(lambda r: httpx.Response(401, json={"error": {"code": "UNAUTHORIZED", "message": "no"}})).send(ROOM, "답", client_msg_id="c")
    assert TOKEN not in str(e.value) and TOKEN not in repr(e.value)


async def test_D11_the_room_id_is_percent_encoded_in_the_send_path():
    seen = {}

    def handler(request):
        seen["raw"] = request.url.raw_path.decode()
        return httpx.Response(201, json=created())

    await api_with(handler).send("a/b c", "답", client_msg_id="c")
    assert "/chat/rooms/a%2Fb%20c/messages" in seen["raw"]
