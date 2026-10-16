"""RequestRunner: 접수·비동기 수행·멱등·동시 수·대화 직렬화·복구 (설계 5.4, 부록 B.8, AC-I1·I14).

실제 SQLite와 가짜 engine을 쓴다. gate(anyio.Event)로 "수행 중" 상태를 붙잡아 경쟁 상황을 만든다.
"""

from __future__ import annotations

import anyio
import pytest

from a4w_inference.engine.models import RunFailed, TurnRequest, Usage
from a4w_inference.runner import (AgentBusy, AgentNotFound, ConversationBusy, IdempotencyConflict, RequestRunner, SubmitRequest,
                                  ValidationFailed)
from a4w_inference.storage.sqlite_db import SqliteInferenceDb
from tests.fakes import FakeEngine, action, result

pytestmark = pytest.mark.anyio


def make(tmp_path, engine, *, max_concurrent=2, text_max=200, requester_max=30):
    db = SqliteInferenceDb(tmp_path / "i.db")
    ids = iter(f"r-{i:04d}" for i in range(1, 1000))
    runner = RequestRunner(engine=engine, db=db, agent_limits={"aiagent": max_concurrent, "other": 1}, text_max_chars=text_max,
                           requester_max_chars=requester_max, now_iso=lambda: "2026-10-15T00:30:00Z", new_id=lambda: next(ids))
    return runner, db


def req(text="회의록 찾아줘", **kw):
    base = dict(agent_id="aiagent", text=text, requester="svc-workfluence")
    base.update(kw)
    return SubmitRequest(**base)


async def test_H1_accept_returns_immediately_with_a_running_row_then_completes(tmp_path):
    gate = anyio.Event()
    engine = FakeEngine(result("찾았습니다"), gate=gate)
    runner, db = make(tmp_path, engine)
    acc = await runner.submit(req(conversation_id="c-1"))
    assert acc.request_id == "r-0001" and acc.status == "running" and acc.replayed is False and acc.conversation_id == "c-1"
    assert db.get("r-0001").status == "running"                  # 수행이 끝나기 전에 행이 먼저 저장돼 있다
    gate.set()
    await runner.drain()
    rec = db.get("r-0001")
    assert rec.status == "done" and rec.answer == "찾았습니다" and rec.complete is True and rec.usage.steps == 2
    assert engine.seen == [TurnRequest(agent_id="aiagent", text="회의록 찾아줘", requester="svc-workfluence", conversation_id="c-1")]


@pytest.mark.parametrize("kw", [dict(text=""), dict(text="   \n\t "), dict(text="가" * 201), dict(requester=""),
                                dict(requester="r" * 31), dict(conversation_id="한글 대화"), dict(conversation_id="a b"),
                                dict(conversation_id="x" * 129), dict(idempotency_key="bad key!")])
async def test_H2_validation_failures(tmp_path, kw):
    runner, db = make(tmp_path, FakeEngine())
    with pytest.raises(ValidationFailed):
        await runner.submit(req(**kw))
    assert db.get("r-0001") is None                              # 거절된 요청은 행을 남기지 않는다


async def test_H2_unknown_agent(tmp_path):
    runner, _ = make(tmp_path, FakeEngine())
    with pytest.raises(AgentNotFound):
        await runner.submit(req(agent_id="ghost"))


async def test_H4_same_idempotency_key_and_text_is_replayed_without_running_again(tmp_path):
    engine = FakeEngine(result("한 번만"))
    runner, db = make(tmp_path, engine)
    first = await runner.submit(req(idempotency_key="req-9f2a"))
    await runner.drain()
    again = await runner.submit(req(idempotency_key="req-9f2a"))
    assert again.replayed is True and again.request_id == first.request_id and again.status == "done"
    await runner.drain()
    assert len(engine.seen) == 1


async def test_H4b_replay_while_still_running_returns_the_running_request(tmp_path):
    gate = anyio.Event()
    runner, _ = make(tmp_path, FakeEngine(gate=gate))
    first = await runner.submit(req(idempotency_key="k"))
    again = await runner.submit(req(idempotency_key="k"))
    assert again.replayed and again.request_id == first.request_id and again.status == "running"
    gate.set(); await runner.drain()


async def test_H5_same_key_with_different_text_is_a_conflict(tmp_path):
    engine = FakeEngine()
    runner, _ = make(tmp_path, engine)
    await runner.submit(req("A 요청", idempotency_key="k"))
    with pytest.raises(IdempotencyConflict):
        await runner.submit(req("B 요청", idempotency_key="k"))
    await runner.drain()
    assert len(engine.seen) == 1


async def test_H6_idempotency_keys_are_scoped_to_the_agent(tmp_path):
    runner, _ = make(tmp_path, FakeEngine())
    a = await runner.submit(req(idempotency_key="k"))
    b = await runner.submit(req(agent_id="other", idempotency_key="k"))
    assert a.request_id != b.request_id and not b.replayed
    await runner.drain()


async def test_H7_a_running_conversation_rejects_the_next_message_until_it_finishes(tmp_path):
    gate = anyio.Event()
    runner, _ = make(tmp_path, FakeEngine(gate=gate))
    await runner.submit(req(conversation_id="c-1"))
    with pytest.raises(ConversationBusy):
        await runner.submit(req("다음 메시지", conversation_id="c-1"))
    other = await runner.submit(req("다른 대화", conversation_id="c-2"))          # 다른 대화는 된다
    assert other.status == "running"
    gate.set(); await runner.drain()
    again = await runner.submit(req("이어서", conversation_id="c-1"))
    assert again.status in ("running", "done")
    await runner.drain()


async def test_H8_two_simultaneous_submits_to_one_conversation_race_safely(tmp_path):
    gate = anyio.Event()
    runner, db = make(tmp_path, FakeEngine(gate=gate), max_concurrent=5)
    outcomes: list = []

    async def go(text):
        try:
            outcomes.append(("ok", (await runner.submit(req(text, conversation_id="c-race"))).request_id))
        except ConversationBusy:
            outcomes.append(("busy", None))

    async with anyio.create_task_group() as tg:
        for t in ("첫 번째", "두 번째", "세 번째"):
            tg.start_soon(go, t)
    assert sorted(k for k, _ in outcomes) == ["busy", "busy", "ok"]
    assert db.running_in_conversation("aiagent", "c-race")
    gate.set(); await runner.drain()


async def test_H9_agent_concurrency_cap_rejects_without_queueing_and_recovers(tmp_path):
    gate = anyio.Event()
    engine = FakeEngine(gate=gate)
    runner, _ = make(tmp_path, engine, max_concurrent=2)
    await runner.submit(req("하나", conversation_id="a"))
    await runner.submit(req("둘", conversation_id="b"))
    with pytest.raises(AgentBusy):
        await runner.submit(req("셋", conversation_id="c"))
    other = await runner.submit(req("다른 agent는 영향 없음", agent_id="other"))
    assert other.status == "running"
    gate.set(); await runner.drain()
    assert engine.max_running <= 3
    third = await runner.submit(req("셋 다시", conversation_id="c"))
    assert third.status in ("running", "done")
    await runner.drain()


async def test_H10_run_failed_closes_the_row_and_frees_the_slot_keeping_actions(tmp_path):
    w = action("create_page", write=True)
    engine = FakeEngine(RunFailed("LLM_TIMEOUT", "LLM 응답 시간 초과", actions=(w,), usage=Usage(steps=2, write_calls=1)))
    runner, db = make(tmp_path, engine, max_concurrent=1)
    acc = await runner.submit(req(conversation_id="c-1"))
    await runner.drain()
    rec = db.get(acc.request_id)
    assert rec.status == "failed" and rec.error_code == "LLM_TIMEOUT" and rec.actions == (w,) and rec.usage.write_calls == 1
    again = await runner.submit(req("다시", conversation_id="c-1"))               # 슬롯과 대화 모두 풀렸다
    assert again.status in ("running", "done")
    await runner.drain()


async def test_H10b_unexpected_exception_becomes_internal_without_leaking(tmp_path):
    runner, db = make(tmp_path, FakeEngine(RuntimeError("secret-token-123 회의록 비밀")))
    acc = await runner.submit(req())
    await runner.drain()
    rec = db.get(acc.request_id)
    assert rec.status == "failed" and rec.error_code == "INTERNAL"
    assert "secret-token-123" not in (rec.error_message or "") and "회의록 비밀" not in (rec.error_message or "")


async def test_H11_recover_closes_running_rows_as_interrupted_and_does_not_rerun(tmp_path):
    db = SqliteInferenceDb(tmp_path / "i.db")
    db.create_running(request_id="r-old", agent_id="aiagent", requester="x", text="끊긴 요청", conversation_id="c-1",
                      idempotency_key=None, created_at="2026-10-15T00:00:00Z")
    engine = FakeEngine()
    runner = RequestRunner(engine=engine, db=db, agent_limits={"aiagent": 2}, text_max_chars=200, requester_max_chars=30,
                           now_iso=lambda: "2026-10-15T00:30:00Z", new_id=lambda: "r-new")
    assert runner.recover() == 1
    rec = db.get("r-old")
    assert rec.status == "failed" and rec.error_code == "INTERRUPTED"
    assert engine.seen == []
    acc = await runner.submit(req(conversation_id="c-1"))                          # 끊긴 대화도 다시 받을 수 있다
    assert acc.request_id == "r-new"
    await runner.drain()


async def test_H17_limit_stop_is_stored_as_done_incomplete(tmp_path):
    w = action("create_page", write=True)
    runner, db = make(tmp_path, FakeEngine(result(None, complete=False, stop_reason="LIMIT_WRITES", actions=[w])))
    acc = await runner.submit(req())
    await runner.drain()
    rec = db.get(acc.request_id)
    assert (rec.status, rec.complete, rec.stop_reason, rec.answer) == ("done", False, "LIMIT_WRITES", None) and rec.actions == (w,)


async def test_the_next_message_sees_the_previous_answer_as_history(tmp_path):
    """되묻기 → 값 더해 이어 가기가 저장소를 거쳐 실제로 이어지는지: 첫 요청이 끝난 뒤 이력에 들어간다."""
    runner, db = make(tmp_path, FakeEngine(result("페이지 제목은 무엇인가요?")))
    await runner.submit(req("페이지 하나 만들어줘", conversation_id="c-1"))
    await runner.drain()
    [t] = db.history("aiagent", "c-1", limit=5)
    assert (t.text, t.answer) == ("페이지 하나 만들어줘", "페이지 제목은 무엇인가요?")


async def test_get_returns_the_record_or_none(tmp_path):
    runner, _ = make(tmp_path, FakeEngine())
    acc = await runner.submit(req())
    await runner.drain()
    assert runner.get(acc.request_id).status == "done" and runner.get("r-없음") is None


async def test_logs_never_contain_the_request_text_or_answer(tmp_path, caplog):
    caplog.set_level("DEBUG")
    runner, _ = make(tmp_path, FakeEngine(result("답변 비밀 내용")))
    await runner.submit(req("요청 비밀 내용"))
    await runner.drain()
    assert "요청 비밀 내용" not in caplog.text and "답변 비밀 내용" not in caplog.text
    assert "r-0001" in caplog.text                                # 요청 id·상태 같은 메타 정보만 남는다


async def test_chat_fields_reach_the_engine_and_default_to_the_phase4_behaviour(tmp_path):
    """Phase 5 S9: 채널이 정한 쓰기 허용·덧붙임은 engine까지 그대로 간다. 주지 않으면 기본값."""
    engine = FakeEngine(result())
    runner, _ = make(tmp_path, engine)
    await runner.submit(req(conversation_id="c-1", allow_writes=False, channel_notes="짧게"))
    await runner.submit(req(conversation_id="c-2"))
    await runner.drain()
    assert (engine.seen[0].allow_writes, engine.seen[0].channel_notes) == (False, "짧게")
    assert (engine.seen[1].allow_writes, engine.seen[1].channel_notes) == (True, None)
