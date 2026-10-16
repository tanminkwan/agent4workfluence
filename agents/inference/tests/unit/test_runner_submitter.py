"""RunnerSubmitter — 채팅 채널의 RequestSubmitter를 Phase 4 runner에 잇는 어댑터 (Phase 5 설계 5.1절, 16절 1번).

실제 RequestRunner·SQLite와 가짜 engine을 쓴다. 채팅 채널은 runner를 모르고 ChatSubmit·ChatOutcome·SubmitRejected만 안다.
"""

from __future__ import annotations

import anyio
import pytest

from a4w_inference.adapters.runner_submitter import RunnerSubmitter
from a4w_inference.channels.chat.ports import ChatOutcome, ChatSubmit, RequestSubmitter, SubmitRejected
from a4w_inference.engine.models import RunFailed, TurnRequest
from a4w_inference.runner import RequestRunner
from a4w_inference.storage.sqlite_db import SqliteInferenceDb
from tests.fakes import FakeEngine, result

pytestmark = pytest.mark.anyio


def make(tmp_path, engine, *, max_concurrent=1, text_max=200):
    db = SqliteInferenceDb(tmp_path / "i.db")
    ids = iter(f"r-{i:04d}" for i in range(1, 1000))
    runner = RequestRunner(engine=engine, db=db, agent_limits={"aiagent": max_concurrent}, text_max_chars=text_max,
                           requester_max_chars=64, now_iso=lambda: "2026-10-16T00:00:00Z", new_id=lambda: next(ids))
    return RunnerSubmitter(runner), runner


def chat(text="회의록 찾아줘", **kw):
    base = dict(agent_id="aiagent", text=text, requester="민지", conversation_id="chat:room-1", idempotency_key="chat:room-1:7",
                allow_writes=False, channel_notes="평문으로 짧게")
    base.update(kw)
    return ChatSubmit(**base)


def test_it_is_a_request_submitter():
    assert issubclass(RunnerSubmitter, RequestSubmitter)


async def test_R1_submit_passes_every_field_to_the_engine_and_returns_the_request_id(tmp_path):
    engine = FakeEngine(result("찾았습니다"))
    sub, runner = make(tmp_path, engine)
    rid = await sub.submit(chat())
    await runner.drain()
    assert rid == "r-0001"
    assert engine.seen == [TurnRequest(agent_id="aiagent", text="회의록 찾아줘", requester="민지", conversation_id="chat:room-1",
                                       allow_writes=False, channel_notes="평문으로 짧게")]


async def test_R2_allowed_writes_reach_the_engine(tmp_path):
    engine = FakeEngine(result())
    sub, runner = make(tmp_path, engine)
    await sub.submit(chat(allow_writes=True, channel_notes=None))
    await runner.drain()
    assert engine.seen[0].allow_writes is True and engine.seen[0].channel_notes is None


async def test_R3_same_idempotency_key_returns_the_same_request_without_running_again(tmp_path):
    engine = FakeEngine(result())
    sub, runner = make(tmp_path, engine)
    first = await sub.submit(chat())
    await runner.drain()
    again = await sub.submit(chat())
    await runner.drain()
    assert again == first and len(engine.seen) == 1


@pytest.mark.parametrize("kw, code", [(dict(text="   "), "VALIDATION_ERROR"), (dict(text="가" * 201), "VALIDATION_ERROR"),
                                      (dict(agent_id="nobody"), "AGENT_NOT_FOUND")])
async def test_R4_rejections_become_submit_rejected_with_the_runner_code(tmp_path, kw, code):
    sub, _ = make(tmp_path, FakeEngine())
    with pytest.raises(SubmitRejected) as e:
        await sub.submit(chat(**kw))
    assert e.value.code == code


async def test_R5_busy_agent_and_busy_conversation_are_reported_as_busy_codes(tmp_path):
    gate = anyio.Event()
    sub, runner = make(tmp_path, FakeEngine(result(), gate=gate), max_concurrent=1)
    await sub.submit(chat())
    with pytest.raises(SubmitRejected) as same_conv:
        await sub.submit(chat(idempotency_key="chat:room-1:8"))
    with pytest.raises(SubmitRejected) as other_conv:
        await sub.submit(chat(conversation_id="chat:room-2", idempotency_key="chat:room-2:1"))
    assert same_conv.value.code == "CONVERSATION_BUSY" and other_conv.value.code == "AGENT_BUSY"
    gate.set()
    await runner.drain()


async def test_R6_get_reports_running_then_done(tmp_path):
    gate = anyio.Event()
    sub, runner = make(tmp_path, FakeEngine(result("다 했습니다"), gate=gate))
    rid = await sub.submit(chat())
    assert await sub.get(rid) == ChatOutcome(status="running", complete=None, stop_reason=None, answer=None, error_code=None)
    gate.set()
    await runner.drain()
    assert await sub.get(rid) == ChatOutcome(status="done", complete=True, stop_reason=None, answer="다 했습니다", error_code=None)


async def test_R7_get_reports_incomplete_stop_and_failure(tmp_path):
    sub, runner = make(tmp_path, FakeEngine(result(None, complete=False, stop_reason="LIMIT_STEPS"), RunFailed("MCP_UNAVAILABLE", "없음")),
                       max_concurrent=2)
    a = await sub.submit(chat(idempotency_key="k-1", conversation_id="chat:a"))
    await runner.drain()
    b = await sub.submit(chat(idempotency_key="k-2", conversation_id="chat:b"))
    await runner.drain()
    assert await sub.get(a) == ChatOutcome(status="done", complete=False, stop_reason="LIMIT_STEPS", answer=None, error_code=None)
    out = await sub.get(b)
    assert out.status == "failed" and out.error_code == "MCP_UNAVAILABLE" and out.answer is None


async def test_R8_unknown_request_is_none(tmp_path):
    sub, _ = make(tmp_path, FakeEngine())
    assert await sub.get("r-9999") is None
