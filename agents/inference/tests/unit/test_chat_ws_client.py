"""WsConnection: 연결 한 번의 수명 (Phase 5 설계 4.1절, FR-C01·C02, AC-C1).

실제 WebSocket(로컬 가짜 서버)으로 확인한다. 재연결은 이 모듈이 아니라 감독(supervisor) 몫이라, 연결이 끝나면
ConnectionEnded로 끝난 이유(닫힘 코드·HTTP 상태·네트워크)만 알린다. 어떤 끝남도 다른 예외로 새지 않는다.
"""

from __future__ import annotations

import json

import anyio
import pytest

from a4w_inference.channels.chat.models import BadFrame, HelloFrame, MessageFrame, RoomFrame, UnknownFrame
from a4w_inference.channels.chat.ws_client import ConnectionEnded, WsConnection, chat_ws_url
from tests.chat_fakes import ME, ROOM, fake_ws_server, wire_message

pytestmark = pytest.mark.anyio
TOKEN = "tok-123"


async def collect(conn):
    got, end = [], None
    try:
        async for f in conn.frames():
            got.append(f)
    except ConnectionEnded as e:
        end = e
    return got, end


def hello():
    return json.dumps({"type": "hello", "userId": ME, "roomIds": [ROOM]})


# ── 주소 ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("base,want", [
    ("https://wf.internal:8444/api/v1", "wss://wf.internal:8444/api/v1/chat/ws"),
    ("https://wf.internal:8444/api/v1/", "wss://wf.internal:8444/api/v1/chat/ws"),
    ("http://127.0.0.1:9000/api/v1", "ws://127.0.0.1:9000/api/v1/chat/ws"),
])
def test_W1_ws_url_is_derived_from_the_rest_base_url(base, want):
    assert chat_ws_url(base) == want


def test_W2_a_base_url_that_is_not_http_is_a_config_error():
    for bad in ("ftp://x/api/v1", "wf.internal/api/v1", ""):
        with pytest.raises(ValueError):
            chat_ws_url(bad)


# ── 연결·프레임 ───────────────────────────────────────────────────────────

async def test_W3_token_goes_in_the_authorization_header_never_in_the_url():
    async def script(ws):
        await ws.send(hello())
        await ws.close(1000)

    async with fake_ws_server(script, expect_token=TOKEN) as server:
        got, end = await collect(WsConnection(server.url, TOKEN))
    assert server.seen[0]["authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in server.seen[0]["path"] and "?" not in server.seen[0]["path"]
    assert isinstance(got[0], HelloFrame) and got[0].user_id == ME


async def test_W4_frames_are_parsed_in_order_and_bad_or_unknown_ones_are_yielded_not_raised():
    async def script(ws):
        await ws.send(hello())
        await ws.send(json.dumps(wire_message(1)))
        await ws.send(json.dumps({"type": "room", "roomId": ROOM, "change": "members"}))
        await ws.send(json.dumps({"type": "typing", "roomId": ROOM}))        # 모르는 type
        await ws.send("this is not json")                                    # 깨진 글
        await ws.send(b"\x00\x01binary")                                     # 바이너리
        await ws.close(1000)

    async with fake_ws_server(script) as server:
        got, end = await collect(WsConnection(server.url, TOKEN))
    kinds = [type(f) for f in got]
    assert kinds == [HelloFrame, MessageFrame, RoomFrame, UnknownFrame, BadFrame, BadFrame]
    assert got[1].message.seq == 1 and got[3].type == "typing"
    assert end.kind == "closed" and end.code == 1000


@pytest.mark.parametrize("code", [1000, 1001, 4400, 4401, 4409, 1009])
async def test_W5_the_close_code_is_reported_after_the_pending_frames(code):
    async def script(ws):
        await ws.send(hello())
        await ws.send(json.dumps(wire_message(2)))
        await ws.close(code)

    async with fake_ws_server(script) as server:
        got, end = await collect(WsConnection(server.url, TOKEN))
    assert [type(f) for f in got] == [HelloFrame, MessageFrame]
    assert isinstance(end, ConnectionEnded) and end.kind == "closed" and end.code == code and end.http_status is None


async def test_W6_an_abrupt_drop_is_reported_as_closed_1006():
    async def script(ws):
        await ws.send(hello())
        ws.transport.abort()
        await anyio.sleep(0.2)

    async with fake_ws_server(script) as server:
        got, end = await collect(WsConnection(server.url, TOKEN))
    assert isinstance(got[0], HelloFrame)
    assert end.kind == "closed" and end.code == 1006


# ── 연결 단계 거절 (HTTP 상태) ────────────────────────────────────────────

@pytest.mark.parametrize("status", [401, 403, 404, 500])
async def test_W7_a_rejected_upgrade_reports_the_http_status(status):
    async def script(ws):          # 닿지 않는다
        raise AssertionError("handshake가 거절돼야 한다")

    async with fake_ws_server(script, reject=status) as server:
        got, end = await collect(WsConnection(server.url, TOKEN))
    assert got == [] and end.kind == "rejected" and end.http_status == status and end.code is None


async def test_W8_a_wrong_token_is_rejected_with_401():
    async def script(ws):
        raise AssertionError("handshake가 거절돼야 한다")

    async with fake_ws_server(script, expect_token="other") as server:
        got, end = await collect(WsConnection(server.url, TOKEN))
    assert end.kind == "rejected" and end.http_status == 401


async def test_W9_connection_refused_is_reported_as_a_network_end():
    got, end = await collect(WsConnection("ws://127.0.0.1:1/api/v1/chat/ws", TOKEN, open_timeout=2.0))
    assert got == [] and end.kind == "network" and end.code is None and end.http_status is None


async def test_W10_a_slow_handshake_times_out_as_a_network_end():
    from websockets.asyncio.server import serve

    async def never(ws):
        await anyio.sleep(5)

    async def slow_request(connection, request):
        await anyio.sleep(5)

    async with serve(never, "127.0.0.1", 0, process_request=slow_request) as server:
        port = server.sockets[0].getsockname()[1]
        got, end = await collect(WsConnection(f"ws://127.0.0.1:{port}/api/v1/chat/ws", TOKEN, open_timeout=0.3))
    assert end.kind == "network"


# ── 수명 ──────────────────────────────────────────────────────────────────

async def test_W11_leaving_the_loop_early_closes_the_socket():
    closed = anyio.Event()

    async def script(ws):
        await ws.send(hello())
        await ws.send(json.dumps(wire_message(1)))
        try:
            await ws.wait_closed()
        finally:
            closed.set()

    async with fake_ws_server(script) as server:
        conn = WsConnection(server.url, TOKEN)
        async for f in conn.frames():
            if isinstance(f, MessageFrame):
                break
        with anyio.fail_after(3):
            await closed.wait()


async def test_W12_keepalive_settings_reach_the_websocket_client():
    """서버 ping에 pong을 자동으로 응답하고(라이브러리 기본), 우리 쪽 keepalive로 조용히 죽은 연결을 끊는다 (설계 4.1)."""
    from websockets.asyncio.client import connect as real_connect
    seen = {}

    def spy(uri, **kwargs):
        seen.update(kwargs)
        return real_connect(uri, **kwargs)

    async def script(ws):
        await ws.send(hello())
        await ws.close(1000)

    async with fake_ws_server(script) as server:
        await collect(WsConnection(server.url, TOKEN, ping_interval=7.0, ping_timeout=9.0, open_timeout=4.0, max_frame_bytes=65536,
                                   connect=spy))
    assert seen["ping_interval"] == 7.0 and seen["ping_timeout"] == 9.0 and seen["open_timeout"] == 4.0
    assert seen["max_size"] == 65536
    assert seen["additional_headers"]["Authorization"] == f"Bearer {TOKEN}"


async def test_W13_the_token_is_not_in_the_repr_or_in_the_end_error():
    conn = WsConnection("ws://127.0.0.1:1/api/v1/chat/ws", TOKEN, open_timeout=1.0)
    assert TOKEN not in repr(conn) and TOKEN not in str(conn)
    _, end = await collect(conn)
    assert TOKEN not in str(end) and TOKEN not in repr(end)


async def test_W14_a_server_message_with_a_secret_like_body_is_not_in_a_bad_frame_reason():
    async def script(ws):
        await ws.send(json.dumps({"type": "message", "roomId": ROOM, "seq": "x", "senderId": ME, "body": "비밀 본문", "kind": "user",
                                  "createdAt": "t"}))
        await ws.close(1000)

    async with fake_ws_server(script) as server:
        got, _ = await collect(WsConnection(server.url, TOKEN))
    assert isinstance(got[0], BadFrame) and "비밀 본문" not in got[0].reason
