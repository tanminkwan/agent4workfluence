"""ConnectionSupervisor: 연결 유지와 재연결 (Phase 5 설계 4.1절, FR-C01·C02, AC-C1).

가짜 연결(스크립트)과 가짜 sleep으로 backoff·상태 전이·카운터를 결정적으로 본다. 실제 소켓은 쓰지 않는다.
"""

from __future__ import annotations

import asyncio

import pytest

from a4w_inference.channels.chat.models import BadFrame, HelloFrame, MessageFrame, RoomFrame, UnknownFrame
from a4w_inference.channels.chat.supervisor import ConnectionSupervisor
from a4w_inference.channels.chat.ws_client import ConnectionEnded
from tests.chat_fakes import ME, ROOM, ScriptedConnection, chat_message, scripted_factory

pytestmark = pytest.mark.anyio

HELLO = HelloFrame(user_id=ME, room_ids=(ROOM,))
MSG = MessageFrame(chat_message(1))


def net():
    return ConnectionEnded("network")


def closed(code):
    return ConnectionEnded("closed", code=code)


def rejected(status):
    return ConnectionEnded("rejected", http_status=status)


def fail():
    return ScriptedConnection([], net())


class Harness:
    """supervisor를 돌리고, sleep이 n번 불리면 멈춘다. delays에 기다린 시간이 남는다."""

    def __init__(self, sessions, *, stop_after=None, handler=None, initial=1.0, maximum=60.0, jitter=None, conflict_limit=3):
        self.delays: list[float] = []
        self.states: list[str] = []
        self.handled: list = []
        self.factory = scripted_factory(*sessions)
        self.stop_after = stop_after if stop_after is not None else len(sessions)
        self.sup = ConnectionSupervisor(self.factory, handler or self._handle, backoff_initial=initial, backoff_max=maximum,
                                        jitter=jitter or (lambda d: d), sleep=self._sleep, conflict_limit=conflict_limit)

    async def _handle(self, frame):
        self.handled.append(frame)

    async def _sleep(self, seconds):
        self.delays.append(seconds)
        self.states.append(self.sup.state)
        if len(self.delays) >= self.stop_after:
            self.sup.stop()

    async def run(self):
        await asyncio.wait_for(self.sup.run(), timeout=5)
        return self


async def test_B1_backoff_doubles_up_to_the_maximum():
    h = await Harness([fail() for _ in range(6)], maximum=10.0).run()
    assert h.delays == [1.0, 2.0, 4.0, 8.0, 10.0, 10.0]


async def test_B2_a_successful_connect_resets_the_backoff():
    """hello를 받은 연결이 끊기면 처음 간격으로 돌아간다."""
    h = await Harness([fail(), fail(), ScriptedConnection([HELLO], closed(1006)), fail()]).run()
    assert h.delays == [1.0, 2.0, 1.0, 2.0]


async def test_B3_jitter_is_applied_to_every_delay():
    h = await Harness([fail(), fail()], jitter=lambda d: d + 0.5).run()
    assert h.delays == [1.5, 2.5]


async def test_B4_state_goes_connecting_connected_reconnecting():
    seen = []
    sessions = [ScriptedConnection([HELLO, MSG], closed(1001))]
    h = Harness(sessions)
    assert h.sup.state == "idle"

    async def handler(frame):
        seen.append((type(frame).__name__, h.sup.state))

    h.sup._handler = handler
    await h.run()
    assert seen == [("HelloFrame", "connected"), ("MessageFrame", "connected")]
    assert h.states == ["reconnecting"] and h.sup.state == "stopped"


async def test_B5_valid_frames_reach_the_handler_in_order_and_unknown_or_bad_ones_are_only_counted():
    room = RoomFrame(ROOM, "members")
    frames = [HELLO, UnknownFrame("typing"), MSG, BadFrame("seq"), room, UnknownFrame("x")]
    h = await Harness([ScriptedConnection(frames, closed(1001))]).run()
    assert h.handled == [HELLO, MSG, room]
    assert h.sup.stats.unknown_frames == 2 and h.sup.stats.bad_frames == 1


async def test_B6_every_end_after_the_first_connect_counts_as_a_reconnect():
    h = await Harness([fail(), ScriptedConnection([HELLO], closed(1006)), fail()]).run()
    assert h.sup.stats.reconnects == 3


@pytest.mark.parametrize("end", [closed(1001), closed(1006), closed(1000), closed(1009), net(), rejected(500), rejected(502)])
async def test_B7_ordinary_ends_just_reconnect_with_the_normal_backoff(end):
    h = await Harness([ScriptedConnection([], end), fail()]).run()
    assert h.delays == [1.0, 2.0] and h.states[0] == "reconnecting"


@pytest.mark.parametrize("end,state", [(closed(4401), "auth_failed"), (rejected(401), "auth_failed"), (rejected(403), "auth_failed"),
                                       (closed(4400), "protocol_error"), (rejected(404), "chat_disabled")])
async def test_B8_unrecoverable_ends_set_a_state_and_wait_the_maximum(end, state):
    """다시 붙어 봐야 같은 결과다. 상태를 올리고 가장 긴 간격으로 쉰다(사람이 token·설정을 고칠 시간을 둔다)."""
    h = await Harness([ScriptedConnection([], end)], maximum=30.0).run()
    assert h.delays == [30.0] and h.states == [state]


async def test_B9_one_or_two_4409_in_a_row_use_the_normal_backoff():
    h = await Harness([ScriptedConnection([], closed(4409)), ScriptedConnection([], closed(4409))]).run()
    assert h.delays == [1.0, 2.0] and h.states == ["reconnecting", "reconnecting"]


async def test_B10_three_4409_in_a_row_mean_a_conflict_and_the_maximum_wait():
    """같은 계정이 다른 곳에서도 연결 중일 수 있다. 서로 밀어내지 않도록 쉰다 (설계 4.1)."""
    h = await Harness([ScriptedConnection([], closed(4409)) for _ in range(3)], maximum=45.0).run()
    assert h.delays == [1.0, 2.0, 45.0] and h.states[-1] == "conflict"


async def test_B11_a_hello_between_4409s_resets_the_conflict_count():
    s = [ScriptedConnection([], closed(4409)), ScriptedConnection([], closed(4409)), ScriptedConnection([HELLO], closed(4409)),
         ScriptedConnection([], closed(4409))]
    h = await Harness(s).run()
    assert "conflict" not in h.states


async def test_B12_the_conflict_limit_is_configurable():
    h = await Harness([ScriptedConnection([], closed(4409)) for _ in range(2)], conflict_limit=2, maximum=9.0).run()
    assert h.delays[-1] == 9.0 and h.states[-1] == "conflict"


async def test_B13_a_handler_error_ends_the_session_and_reconnects_without_dying():
    calls = []

    async def handler(frame):
        calls.append(type(frame).__name__)
        if isinstance(frame, MessageFrame):
            raise RuntimeError("처리 중 오류")

    h = await Harness([ScriptedConnection([HELLO, MSG, MSG], closed(1006)), ScriptedConnection([HELLO], closed(1006))],
                      handler=handler).run()
    assert calls == ["HelloFrame", "MessageFrame", "HelloFrame"]       # 오류난 연결의 나머지 프레임은 버리고 새 연결에서 보충한다
    assert h.sup.stats.handler_errors == 1 and h.delays == [1.0, 1.0]


async def test_B14_the_failed_session_is_closed_before_the_next_attempt():
    first = ScriptedConnection([HELLO, MSG], closed(1006))

    async def handler(frame):
        if isinstance(frame, MessageFrame):
            raise RuntimeError("boom")

    await Harness([first, fail()], handler=handler).run()
    assert first.closed is True


async def test_B15_stop_from_inside_the_handler_ends_the_loop_and_closes_the_session():
    conn = ScriptedConnection([HELLO, MSG, MSG, MSG], closed(1006))
    holder = {}

    async def handler(frame):
        if isinstance(frame, MessageFrame):
            holder["h"].sup.stop()

    h = Harness([conn], handler=handler, stop_after=99)
    holder["h"] = h
    await h.run()
    assert conn.closed is True and h.sup.state == "stopped" and h.delays == []


async def test_B16_cancelling_while_waiting_propagates_cancellation_promptly():
    gate = asyncio.Event()

    async def hang(_seconds):
        await gate.wait()

    sup = ConnectionSupervisor(scripted_factory(fail()), lambda f: asyncio.sleep(0), backoff_initial=1.0, backoff_max=5.0,
                               jitter=lambda d: d, sleep=hang)
    task = asyncio.create_task(sup.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)


async def test_B17_a_connection_factory_error_is_treated_like_a_network_end():
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("factory failed")
        return fail()

    delays = []

    async def sleep(seconds):
        delays.append(seconds)
        if len(delays) >= 2:
            sup.stop()

    sup = ConnectionSupervisor(factory, lambda f: asyncio.sleep(0), backoff_initial=1.0, backoff_max=5.0, jitter=lambda d: d, sleep=sleep)
    await asyncio.wait_for(sup.run(), timeout=5)
    assert delays == [1.0, 2.0]


async def test_B18_stats_expose_the_last_end_reason_without_secrets():
    h = await Harness([ScriptedConnection([HELLO], closed(4409))]).run()
    assert h.sup.stats.last_end == "closed:4409"
    h2 = await Harness([ScriptedConnection([], rejected(401))]).run()
    assert h2.sup.stats.last_end == "rejected:401"
    h3 = await Harness([fail()]).run()
    assert h3.sup.stats.last_end == "network"
