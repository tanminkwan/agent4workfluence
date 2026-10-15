"""SQLite 기록·이력 저장소 (설계 8·11절, 부록 B.2 B6·B7, B.8 H11~H13). TDD red."""

from __future__ import annotations

import sqlite3

import pytest

from a4w_inference.engine.models import ActionSummary, RunFailed, Usage
from a4w_inference.storage.sqlite_db import SqliteInferenceDb
from tests.fakes import result

T0 = "2026-10-15T00:30:00Z"


@pytest.fixture
def db(tmp_path):
    return SqliteInferenceDb(tmp_path / "inference.db")


def add(db, rid, text="요청", *, agent="aiagent", conv="c-1", key=None, requester="svc", at=T0):
    db.create_running(request_id=rid, agent_id=agent, requester=requester, text=text, conversation_id=conv,
                      idempotency_key=key, created_at=at)


def done(db, rid, answer="답", at="2026-10-15T00:31:00Z", **kw):
    db.finish_done(rid, result(answer, **kw), finished_at=at, elapsed_ms=1000)


def test_H1_running_row_is_saved_first_then_completed_with_actions_and_usage(db):
    add(db, "r-1", "회의록 찾아줘")
    rec = db.get("r-1")
    assert rec.status == "running" and rec.answer is None and rec.text == "회의록 찾아줘"
    act = ActionSummary(tool="workfluence__search_pages", revision="r-1", ok=True, error_code=None, target_request_id="wf-1",
                        write=False, args={"q": "회의록"})
    db.finish_done("r-1", result("찾았습니다", actions=[act]), finished_at="2026-10-15T00:31:00Z", elapsed_ms=8421)
    rec = db.get("r-1")
    assert (rec.status, rec.complete, rec.stop_reason, rec.answer, rec.elapsed_ms) == ("done", True, None, "찾았습니다", 8421)
    assert rec.actions == (act,) and rec.usage.prompt_tokens == 1200 and rec.usage.steps == 2
    assert rec.finished_at == "2026-10-15T00:31:00Z"


def test_limit_stop_is_done_but_incomplete_and_keeps_actions(db):
    add(db, "r-1")
    a = ActionSummary("workfluence__create_page", "r-1", True, None, "wf-2", True, {"title": "공지"})
    db.finish_done("r-1", result(None, complete=False, stop_reason="LIMIT_WRITES", actions=[a]), finished_at=T0, elapsed_ms=5)
    rec = db.get("r-1")
    assert rec.status == "done" and rec.complete is False and rec.stop_reason == "LIMIT_WRITES" and rec.answer is None
    assert rec.actions == (a,)


def test_failed_row_keeps_error_and_prior_writes(db):
    add(db, "r-1")
    a = ActionSummary("workfluence__create_page", "r-1", True, None, "wf-2", True, {})
    db.finish_failed("r-1", code="LLM_TIMEOUT", message="LLM 응답 시간 초과", actions=[a], usage=Usage(steps=2, write_calls=1),
                     finished_at=T0, elapsed_ms=9)
    rec = db.get("r-1")
    assert rec.status == "failed" and (rec.error_code, rec.error_message) == ("LLM_TIMEOUT", "LLM 응답 시간 초과")
    assert rec.actions == (a,) and rec.usage.write_calls == 1


def test_get_missing_is_none(db):
    assert db.get("nope") is None


def test_H13_history_is_done_rows_with_an_answer_oldest_first_and_latest_n(db):
    for i, at in enumerate(["2026-10-15T00:01:00Z", "2026-10-15T00:02:00Z", "2026-10-15T00:03:00Z", "2026-10-15T00:04:00Z"], 1):
        add(db, f"r-{i}", f"요청{i}", at=at)
        done(db, f"r-{i}", f"답{i}")
    h = db.history("aiagent", "c-1", limit=3)
    assert [t.text for t in h] == ["요청2", "요청3", "요청4"] and [t.answer for t in h] == ["답2", "답3", "답4"]


def test_B7_failed_limited_and_running_requests_are_not_history(db):
    add(db, "r-ok", "정상", at="2026-10-15T00:01:00Z"); done(db, "r-ok", "정상 답")
    add(db, "r-fail", "실패", at="2026-10-15T00:02:00Z")
    db.finish_failed("r-fail", code="LLM_AUTH", message="x", actions=[], usage=Usage(), finished_at=T0, elapsed_ms=1)
    add(db, "r-lim", "한도", at="2026-10-15T00:03:00Z")
    db.finish_done("r-lim", result(None, complete=False, stop_reason="LIMIT_STEPS"), finished_at=T0, elapsed_ms=1)
    add(db, "r-run", "진행 중", at="2026-10-15T00:04:00Z")
    assert [t.text for t in db.history("aiagent", "c-1", limit=10)] == ["정상"]


def test_B6_history_is_isolated_by_agent_and_by_conversation(db):
    add(db, "r-1", "내 대화", agent="aiagent", conv="c-1"); done(db, "r-1")
    add(db, "r-2", "다른 대화", agent="aiagent", conv="c-2"); done(db, "r-2")
    add(db, "r-3", "다른 agent", agent="other", conv="c-1"); done(db, "r-3")
    assert [t.text for t in db.history("aiagent", "c-1", limit=10)] == ["내 대화"]
    assert db.history("aiagent", "없는 대화", limit=10) == []


def test_history_turn_carries_action_summaries_but_never_args_or_results(db):
    add(db, "r-1"); 
    a = ActionSummary("workfluence__list_notifications", "r-1", True, None, "wf-1", False, {"q": "x" * 50})
    done(db, "r-1", "알림 2개", actions=[a])
    [t] = db.history("aiagent", "c-1", limit=5)
    assert t.actions[0].tool == "workfluence__list_notifications" and t.actions[0].ok is True


def test_idempotency_lookup_is_per_agent(db):
    add(db, "r-1", "같은 요청", key="k-1")
    add(db, "r-2", "같은 key 다른 agent", agent="other", key="k-1", conv=None)
    assert db.find_by_key("aiagent", "k-1").request_id == "r-1"
    assert db.find_by_key("other", "k-1").request_id == "r-2"
    assert db.find_by_key("aiagent", "없음") is None


def test_unique_idempotency_key_per_agent_is_enforced_by_the_database(db):
    add(db, "r-1", key="k-1")
    with pytest.raises(sqlite3.IntegrityError):
        add(db, "r-2", key="k-1")


def test_running_in_conversation(db):
    assert db.running_in_conversation("aiagent", "c-1") is False
    add(db, "r-1")
    assert db.running_in_conversation("aiagent", "c-1") is True
    assert db.running_in_conversation("aiagent", "c-2") is False
    assert db.running_in_conversation("other", "c-1") is False
    done(db, "r-1")
    assert db.running_in_conversation("aiagent", "c-1") is False


def test_H11_mark_interrupted_closes_only_running_rows(db):
    add(db, "r-run", conv="c-1"); add(db, "r-done", conv="c-2"); done(db, "r-done")
    assert db.mark_interrupted(finished_at="2026-10-15T01:00:00Z") == 1
    rec = db.get("r-run")
    assert rec.status == "failed" and rec.error_code == "INTERRUPTED" and rec.finished_at == "2026-10-15T01:00:00Z"
    assert db.get("r-done").status == "done"
    assert db.mark_interrupted(finished_at="2026-10-15T01:00:01Z") == 0


def test_H12_purge_removes_old_finished_rows_and_their_keys_but_never_running(db):
    add(db, "old-done", key="k-old", at="2026-09-01T00:00:00Z", conv="c-1"); done(db, "old-done")
    add(db, "old-fail", at="2026-09-01T00:00:00Z", conv="c-2")
    db.finish_failed("old-fail", code="INTERNAL", message="x", actions=[], usage=Usage(), finished_at=T0, elapsed_ms=1)
    add(db, "old-run", at="2026-09-01T00:00:00Z", conv="c-3")
    add(db, "new-done", at="2026-10-14T00:00:00Z", conv="c-4"); done(db, "new-done")
    assert db.purge(before="2026-09-15T00:00:00Z") == 2
    assert db.get("old-done") is None and db.get("old-fail") is None
    assert db.get("old-run") is not None and db.get("new-done") is not None
    assert db.find_by_key("aiagent", "k-old") is None
    add(db, "again", key="k-old", conv="c-9")                    # 만료된 key는 다시 쓸 수 있다


def test_F7_last_chars_per_token_comes_from_the_latest_finished_run_of_that_agent(db):
    assert db.last_chars_per_token("aiagent") is None
    add(db, "r-1", at="2026-10-15T00:01:00Z"); done(db, "r-1", at="2026-10-15T00:02:00Z")      # 2400자 / 1200토큰 = 2.0
    assert db.last_chars_per_token("aiagent") == pytest.approx(2.0)
    add(db, "r-2", at="2026-10-15T00:03:00Z", conv="c-2")
    u = Usage(prompt_tokens=3000, last_input_tokens=3000, last_input_chars=3000)
    db.finish_done("r-2", result("답", usage=u), finished_at="2026-10-15T00:04:00Z", elapsed_ms=1)
    assert db.last_chars_per_token("aiagent") == pytest.approx(1.0)
    assert db.last_chars_per_token("other") is None


def test_rows_without_a_measurement_are_ignored_for_calibration(db):
    add(db, "r-1")
    db.finish_done("r-1", result("답", usage=Usage()), finished_at=T0, elapsed_ms=1)
    assert db.last_chars_per_token("aiagent") is None


def test_data_survives_reopening_the_file(tmp_path):
    p = tmp_path / "x.db"
    a = SqliteInferenceDb(p); add(a, "r-1"); done(a, "r-1", "남아야 한다")
    b = SqliteInferenceDb(p)
    assert b.get("r-1").answer == "남아야 한다"
    assert [t.answer for t in b.history("aiagent", "c-1", limit=5)] == ["남아야 한다"]


def test_no_tool_arguments_or_result_bodies_are_stored_beyond_the_capped_summary(db, tmp_path):
    add(db, "r-1")
    a = ActionSummary("workfluence__create_page", "r-1", True, None, "wf-1", True, {"body": "짧은 요약…(5000자)"})
    done(db, "r-1", actions=[a])
    raw = (tmp_path / "inference.db").read_bytes()
    assert "짧은 요약".encode() in raw and b"api_token" not in raw


@pytest.mark.anyio
async def test_the_async_adapter_implements_the_conversation_store_port(db):
    from a4w_inference.adapters.store_async import ThreadedConversationStore
    from a4w_inference.engine.ports import ConversationStore
    add(db, "r-1"); done(db, "r-1", "답1")
    store = ThreadedConversationStore(db)
    assert isinstance(store, ConversationStore)
    assert [t.answer for t in await store.history("aiagent", "c-1", limit=5)] == ["답1"]
    assert await store.last_chars_per_token("aiagent") == pytest.approx(2.0)

