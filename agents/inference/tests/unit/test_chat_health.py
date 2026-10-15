"""채팅 상태 (Phase 5 설계 11.2절, FR-C42, AC-C10). /health의 chat 항목.

연결 상태·카운터·명세 변경 여부만 담는다. token·메시지 본문·사용자 이름·id는 담지 않는다. 채팅이 나빠도 /health 전체 status는 내리지 않는다
(바깥 시스템 상태를 넣지 않는 Phase 4 규칙과 같다).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import anyio
import httpx
import pytest

from a4w_inference.channels.chat.dispatcher import DispatcherStats
from a4w_inference.channels.chat.health import ChatHealth
from a4w_inference.channels.chat.models import MessageFrame, UnknownFrame
from a4w_inference.channels.chat.store import SqliteChatState
from a4w_inference.channels.chat.supervisor import ConnectionSupervisor, SupervisorStats
from a4w_inference.channels.chat.worker import WorkerStats
from a4w_inference.channels.chat.ws_client import ConnectionEnded
from tests.chat_fakes import NOW_ISO, ROOM, ROOM2, ScriptedConnection, chat_message, scripted_factory

pytestmark = pytest.mark.anyio
EXPECTED_KEYS = {"state", "rooms", "lastFrameAt", "lastEnd", "reconnects", "handlerErrors", "unknownFrames", "badFrames",
                 "unknownRoomChanges", "catchupFailures", "staleSkipped", "sinkErrors", "worker"}


def make(tmp_path, *, state="connected", sup_stats=None):
    state_db = SqliteChatState(tmp_path / "chat.db")
    state_db.register_room(ROOM, "direct", start_seq=0, now=NOW_ISO)
    state_db.register_room(ROOM2, "group", start_seq=0, now=NOW_ISO)
    supervisor = SimpleNamespace(state=state, stats=sup_stats or SupervisorStats())
    dispatcher = SimpleNamespace(stats=DispatcherStats())
    worker = SimpleNamespace(stats=WorkerStats())
    return ChatHealth(supervisor=supervisor, dispatcher=dispatcher, worker=worker, state=state_db, now_iso=lambda: NOW_ISO), supervisor


def test_H1_the_snapshot_has_exactly_the_documented_keys_and_is_json_serialisable(tmp_path):
    h, _ = make(tmp_path)
    snap = h.snapshot()
    assert set(snap) == EXPECTED_KEYS
    json.dumps(snap)
    assert snap["state"] == "connected" and snap["rooms"] == 2 and snap["lastFrameAt"] is None and snap["lastEnd"] is None
    assert set(snap["worker"]) == {"completed", "sendFailures", "errors", "queueFull", "timeouts", "busyGiveups"}


def test_H2_counters_are_copied_from_the_components(tmp_path):
    s = SupervisorStats(unknown_frames=3, bad_frames=2, reconnects=5, handler_errors=1, last_end="closed:4409")
    h, sup = make(tmp_path, sup_stats=s)
    h._dispatcher.stats.catchup_failures, h._dispatcher.stats.stale_skipped = 4, 6
    h._dispatcher.stats.unknown_room_changes, h._dispatcher.stats.sink_errors = 7, 8
    h._worker.stats.completed, h._worker.stats.send_failures, h._worker.stats.queue_full = 9, 1, 2
    snap = h.snapshot()
    assert (snap["unknownFrames"], snap["badFrames"], snap["reconnects"], snap["handlerErrors"]) == (3, 2, 5, 1)
    assert snap["lastEnd"] == "closed:4409"
    assert (snap["catchupFailures"], snap["staleSkipped"], snap["unknownRoomChanges"], snap["sinkErrors"]) == (4, 6, 7, 8)
    assert snap["worker"]["completed"] == 9 and snap["worker"]["sendFailures"] == 1 and snap["worker"]["queueFull"] == 2


@pytest.mark.parametrize("state", ["connecting", "connected", "reconnecting", "auth_failed", "protocol_error", "chat_disabled", "conflict", "stopped"])
def test_H3_the_connection_state_is_reported_as_it_is(tmp_path, state):
    h, _ = make(tmp_path, state=state)
    assert h.snapshot()["state"] == state


def test_H5_the_last_frame_time_is_stamped_by_the_tracking_wrapper_even_when_the_handler_fails(tmp_path):
    h, _ = make(tmp_path)
    seen = []

    async def handler(frame):
        seen.append(frame)
        if isinstance(frame, UnknownFrame):
            raise RuntimeError("처리 실패")

    wrapped = h.track(handler)

    async def go():
        assert h.snapshot()["lastFrameAt"] is None
        await wrapped(MessageFrame(chat_message(1)))
        assert h.snapshot()["lastFrameAt"] == NOW_ISO
        with pytest.raises(RuntimeError):
            await wrapped(UnknownFrame("x"))

    anyio.run(go)
    assert len(seen) == 2


def test_H6_the_snapshot_holds_no_message_text_names_ids_or_tokens(tmp_path):
    h, _ = make(tmp_path)
    text = json.dumps(h.snapshot(), ensure_ascii=False)
    assert ROOM not in text and ROOM2 not in text and "tok" not in text.lower()


def test_H7_a_disabled_chat_reports_only_its_state():
    assert ChatHealth.disabled() == {"state": "disabled"}


# ── /health 연결 ──────────────────────────────────────────────────────────

def rest_client(tmp_path, chat_health=None):
    from a4w_inference.channels.rest.app import RestContext, create_app
    from a4w_inference.runner import RequestRunner
    from a4w_inference.storage.sqlite_db import SqliteInferenceDb
    from tests.fakes import FakeEngine, result

    runner = RequestRunner(engine=FakeEngine(result("답")), db=SqliteInferenceDb(tmp_path / "i.db"), agent_limits={"aiagent": 1},
                           text_max_chars=100, requester_max_chars=20, now_iso=lambda: NOW_ISO, new_id=lambda: "r-1")
    ctx = RestContext(runner=runner, auth_mode="none", api_token=None, health=lambda: {"aiagent": {"mcp": "up", "tools": 3}},
                      chat_health=chat_health)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(ctx)), base_url="http://inference.test")


async def test_H9_health_includes_the_chat_section_when_chat_is_wired_and_keeps_status_ok(tmp_path):
    client = rest_client(tmp_path, chat_health=lambda: {"state": "auth_failed", "rooms": 0})
    body = (await client.get("/health")).json()
    assert body["status"] == "ok" and body["agents"] == {"aiagent": {"mcp": "up", "tools": 3}}
    assert body["chat"] == {"state": "auth_failed", "rooms": 0}


async def test_H10_health_without_chat_is_exactly_what_phase_4_returned(tmp_path):
    body = (await rest_client(tmp_path).get("/health")).json()
    assert body == {"status": "ok", "agents": {"aiagent": {"mcp": "up", "tools": 3}}}
