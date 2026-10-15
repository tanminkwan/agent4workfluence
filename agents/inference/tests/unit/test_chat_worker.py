"""ChatWorkerPool: 방별 수행 연결 (Phase 5 설계 3·5.1·6절, FR-C13~C16·C21, AC-C4·C5·C7).

가짜 RequestSubmitter(수행 로직 쪽)와 가짜 ReplySender(보내기 쪽)를 쓰고, 상태는 실제 SQLite다.
기다림(polling·AGENT_BUSY 재시도)은 가짜 sleep이 누적 시간으로 센다. 실제로 쉬지 않는다.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from a4w_inference.channels.chat.models import IncomingRequest
from a4w_inference.channels.chat.ports import ChatOutcome, SubmitRejected
from a4w_inference.channels.chat.store import SqliteChatState
from a4w_inference.channels.chat.worker import ChatWorkerPool, ReplyTexts
from tests.chat_fakes import NOW_ISO, ROOM, ROOM2, chat_message, room_info
from tests.chat_worker_fakes import FakeSender, FakeSubmitter

pytestmark = pytest.mark.anyio

TEXTS = {"answer_empty": "[비어 있음]", "limit_reached": "[한도]", "failed": "[실패]", "rejected": "[거절]", "busy": "[바쁨]",
         "queue_full": "[대기 가득]", "timeout": "[시간 초과]", "interrupted": "[중단됨]"}
STOP_LABELS = {"LIMIT_STEPS": "[차례 한도]", "LIMIT_TIME": "[시간 한도]"}


def incoming(seq, *, room_id=ROOM, body="회의록 찾아줘", requester="tiffanie", allow_writes=False, kind="direct"):
    return IncomingRequest(room=room_info(room_id, kind=kind), message=chat_message(seq, body, room_id=room_id), requester=requester,
                           allow_writes=allow_writes)


class Env:
    def __init__(self, tmp_path, **kw):
        self.state = SqliteChatState(tmp_path / "chat.db")
        self.submitter, self.sender = FakeSubmitter(), FakeSender()
        self.sleeps: list[float] = []
        opts = dict(agent_id="aiagent", poll_interval=1.0, wait_max_seconds=1e9, busy_retry_seconds=2.0, busy_retry_max_seconds=6.0,
                    queue_max=5, notes=None)
        opts.update(kw)
        self.pool = ChatWorkerPool(submitter=self.submitter, sender=self.sender, state=self.state,
                                   replies=ReplyTexts(TEXTS, stop_labels=STOP_LABELS), now_iso=lambda: NOW_ISO, sleep=self._sleep, **opts)

    async def _sleep(self, seconds):
        self.sleeps.append(seconds)
        await asyncio.sleep(0)

    def open_room(self, room_id=ROOM, kind="direct", last=0):
        self.state.register_room(room_id, kind, start_seq=last, now=NOW_ISO)

    async def feed(self, *requests):
        for r in requests:
            self.state.begin(r.room.room_id, r.message.seq, "queued", now=NOW_ISO)     # dispatcher가 한 일
            await self.pool(r)

    async def run(self, *requests):
        await self.feed(*requests)
        await asyncio.wait_for(self.pool.drain(), timeout=5)


def make(tmp_path, **kw):
    env = Env(tmp_path, **kw)
    env.open_room()
    env.open_room(ROOM2)
    return env


# ── 요청 변환 ─────────────────────────────────────────────────────────────

async def test_K1_the_request_is_mapped_to_the_submitter_arguments(tmp_path):
    env = make(tmp_path, notes="채팅 덧붙임")
    await env.run(incoming(5, body="  페이지 만들어줘  ", requester="tiffanie", allow_writes=True))
    (a,) = env.submitter.submits
    assert a.agent_id == "aiagent" and a.text == "페이지 만들어줘" and a.requester == "tiffanie"
    assert a.conversation_id == f"chat:{ROOM}" and a.idempotency_key == f"chat:{ROOM}:5"
    assert a.allow_writes is True and a.channel_notes == "채팅 덧붙임"


async def test_K2_without_notes_none_is_passed_and_writes_default_to_denied(tmp_path):
    env = make(tmp_path)
    await env.run(incoming(1))
    assert env.submitter.submits[0].channel_notes is None and env.submitter.submits[0].allow_writes is False


async def test_K3_the_ids_fit_the_runner_id_rule(tmp_path):
    import re
    env = make(tmp_path)
    await env.run(incoming(123456))
    a = env.submitter.submits[0]
    assert re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", a.conversation_id) and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", a.idempotency_key)


# ── 결과 → 방 응답 ────────────────────────────────────────────────────────

async def test_K4_a_complete_answer_is_posted_with_a_stable_client_msg_id_and_marked_replied(tmp_path):
    env = make(tmp_path)
    await env.run(incoming(1))
    assert env.sender.posts == [(ROOM, "answer-for-r-0001", "reply:r-0001")]
    h = env.state.get_handled(ROOM, 1)
    assert h.state == "replied" and h.request_id == "r-0001" and h.reply_msg_id == "reply:r-0001"


async def test_K5_the_row_is_submitted_while_the_request_is_running(tmp_path):
    env = make(tmp_path)
    seen = []
    orig = env.submitter.outcome_fn

    def spy(rid, n):
        seen.append(env.state.get_handled(ROOM, 1).state)
        return orig(rid, n)

    env.submitter.outcome_fn = spy
    await env.run(incoming(1))
    assert seen == ["submitted"] and env.state.get_handled(ROOM, 1).state == "replied"


async def test_K6_polling_waits_with_the_configured_interval_until_the_request_is_done(tmp_path):
    env = make(tmp_path, poll_interval=0.5)
    env.submitter.outcome_fn = lambda rid, n: ChatOutcome("running", None, None, None, None) if n < 4 else ChatOutcome("done", True, None, "끝", None)
    await env.run(incoming(1))
    assert env.sender.texts() == ["끝"] and env.sleeps == [0.5, 0.5, 0.5] and len(env.submitter.get_calls) == 4


async def test_K7_waiting_too_long_posts_a_timeout_notice_and_abandons_the_row(tmp_path):
    env = make(tmp_path, poll_interval=1.0, wait_max_seconds=3.0)
    env.submitter.outcome_fn = lambda rid, n: ChatOutcome("running", None, None, None, None)
    await env.run(incoming(1))
    assert env.sender.texts() == ["[시간 초과]"] and env.state.get_handled(ROOM, 1).state == "abandoned"
    assert len(env.submitter.submits) == 1                 # 다시 접수하지 않는다(쓰기 중복 방지)
    assert sum(env.sleeps) <= 3.0 + 1.0


async def test_K8_an_incomplete_run_posts_the_limit_text_with_the_stop_reason_label(tmp_path):
    env = make(tmp_path)
    env.submitter.outcome_fn = lambda rid, n: ChatOutcome("done", False, "LIMIT_STEPS", None, None)
    await env.run(incoming(1))
    (text,) = env.sender.texts()
    assert text.startswith("[한도]") and "[차례 한도]" in text
    assert env.state.get_handled(ROOM, 1).state == "replied"


async def test_K9_an_unlabelled_stop_reason_still_posts_the_limit_text(tmp_path):
    env = make(tmp_path)
    env.submitter.outcome_fn = lambda rid, n: ChatOutcome("done", False, "SOMETHING_NEW", None, None)
    await env.run(incoming(1))
    assert env.sender.texts() == ["[한도]"]


async def test_K10_a_failed_run_posts_the_failure_text_and_the_error_code_only(tmp_path):
    env = make(tmp_path)
    env.submitter.outcome_fn = lambda rid, n: ChatOutcome("failed", None, None, None, "MCP_UNAVAILABLE")
    await env.run(incoming(1))
    assert env.sender.texts() == ["[실패] (MCP_UNAVAILABLE)"]


async def test_K11_a_lost_record_is_reported_as_a_failure(tmp_path):
    env = make(tmp_path)
    env.submitter.outcome_fn = lambda rid, n: None
    await env.run(incoming(1))
    assert env.sender.texts() == ["[실패] (REQUEST_LOST)"]


@pytest.mark.parametrize("answer", ["", "   ", None])
async def test_K12_an_empty_answer_posts_the_empty_notice(tmp_path, answer):
    env = make(tmp_path)
    env.submitter.outcome_fn = lambda rid, n: ChatOutcome("done", True, None, answer, None)
    await env.run(incoming(1))
    assert env.sender.texts() == ["[비어 있음]"]


# ── 접수 거절·바쁨 ────────────────────────────────────────────────────────

async def test_K13_a_validation_rejection_posts_the_rejected_text_and_does_not_poll(tmp_path):
    env = make(tmp_path)
    env.submitter.submit_errors = [SubmitRejected("VALIDATION_ERROR", "text가 너무 길다")]
    await env.run(incoming(1))
    assert env.sender.texts() == ["[거절]"] and env.submitter.get_calls == []
    assert env.state.get_handled(ROOM, 1).state == "abandoned"


@pytest.mark.parametrize("code", ["AGENT_NOT_FOUND", "IDEMPOTENCY_CONFLICT", "SOMETHING"])
async def test_K14_other_rejections_post_the_failure_text_with_the_code(tmp_path, code):
    env = make(tmp_path)
    env.submitter.submit_errors = [SubmitRejected(code, "x")]
    await env.run(incoming(1))
    assert env.sender.texts() == [f"[실패] ({code})"]


@pytest.mark.parametrize("code", ["AGENT_BUSY", "CONVERSATION_BUSY"])
async def test_K15_busy_rejections_are_retried_after_the_busy_interval(tmp_path, code):
    env = make(tmp_path, busy_retry_seconds=2.0, busy_retry_max_seconds=6.0)
    env.submitter.submit_errors = [SubmitRejected(code, "바쁨"), SubmitRejected(code, "바쁨")]
    await env.run(incoming(1))
    assert env.sleeps == [2.0, 2.0] and env.sender.texts() == ["answer-for-r-0001"]
    assert env.state.get_handled(ROOM, 1).state == "replied"


async def test_K16_giving_up_on_busy_posts_the_busy_notice_once_and_abandons(tmp_path):
    env = make(tmp_path, busy_retry_seconds=2.0, busy_retry_max_seconds=6.0)
    env.submitter.always_error = SubmitRejected("AGENT_BUSY", "바쁨")
    await env.run(incoming(1))
    assert env.sleeps == [2.0, 2.0, 2.0] and env.sender.texts() == ["[바쁨]"]
    assert env.state.get_handled(ROOM, 1).state == "abandoned" and env.submitter.submits == []


# ── 방별 직렬화 · 방 간 동시 ──────────────────────────────────────────────

async def test_K17_requests_in_one_room_run_one_at_a_time_in_order(tmp_path):
    env = make(tmp_path)
    gate = env.submitter.hold(f"chat:{ROOM}")
    await env.feed(incoming(1), incoming(2))
    await asyncio.sleep(0.05)
    assert [a.idempotency_key for a in env.submitter.submits] == [f"chat:{ROOM}:1"]          # 첫 요청이 끝나기 전에는 둘째를 접수하지 않는다
    gate.set()
    await asyncio.wait_for(env.pool.drain(), timeout=5)
    assert [a.idempotency_key for a in env.submitter.submits] == [f"chat:{ROOM}:1", f"chat:{ROOM}:2"]
    assert env.sender.texts() == ["answer-for-r-0001", "answer-for-r-0002"]


async def test_K18_different_rooms_run_at_the_same_time(tmp_path):
    env = make(tmp_path)
    g1, g2 = env.submitter.hold(f"chat:{ROOM}"), env.submitter.hold(f"chat:{ROOM2}")
    await env.feed(incoming(1), incoming(1, room_id=ROOM2))
    await asyncio.sleep(0.05)
    assert len(env.submitter.submits) == 2
    g1.set()
    g2.set()
    await asyncio.wait_for(env.pool.drain(), timeout=5)
    assert len(env.sender.posts) == 2


async def test_K19_submitting_to_the_pool_returns_without_waiting_for_the_run(tmp_path):
    env = make(tmp_path)
    env.submitter.hold(f"chat:{ROOM}")
    env.state.begin(ROOM, 1, "queued", now=NOW_ISO)
    env.state.begin(ROOM, 2, "queued", now=NOW_ISO)
    await asyncio.wait_for(env.pool(incoming(1)), timeout=1)
    await asyncio.wait_for(env.pool(incoming(2)), timeout=1)
    env.submitter._holds[f"chat:{ROOM}"].set()
    await asyncio.wait_for(env.pool.drain(), timeout=5)


async def test_K20_the_room_queue_is_bounded_and_the_overflow_notice_is_posted_once_per_burst(tmp_path):
    env = make(tmp_path, queue_max=2)
    gate = env.submitter.hold(f"chat:{ROOM}")
    await env.feed(incoming(1), incoming(2), incoming(3), incoming(4))
    await asyncio.sleep(0.05)
    assert env.sender.texts() == ["[대기 가득]"]
    assert env.state.get_handled(ROOM, 3).state == "abandoned" and env.state.get_handled(ROOM, 4).state == "abandoned"
    gate.set()
    await asyncio.wait_for(env.pool.drain(), timeout=5)
    assert [a.idempotency_key for a in env.submitter.submits] == [f"chat:{ROOM}:1", f"chat:{ROOM}:2"]
    # 큐가 비면 다시 받는다
    await env.run(incoming(5))
    assert f"chat:{ROOM}:5" in [a.idempotency_key for a in env.submitter.submits]


async def test_K21_the_overflow_notice_is_posted_again_for_a_later_burst(tmp_path):
    env = make(tmp_path, queue_max=1)
    gate = env.submitter.hold(f"chat:{ROOM}")
    await env.feed(incoming(1), incoming(2))
    await asyncio.sleep(0.05)
    gate.set()
    await asyncio.wait_for(env.pool.drain(), timeout=5)
    env.submitter.hold(f"chat:{ROOM}")
    await env.feed(incoming(3), incoming(4))
    await asyncio.sleep(0.05)
    assert env.sender.texts().count("[대기 가득]") == 2
    env.submitter._holds[f"chat:{ROOM}"].set()
    await asyncio.wait_for(env.pool.drain(), timeout=5)


# ── 오류 격리 ─────────────────────────────────────────────────────────────

async def test_K22_a_post_that_returns_false_abandons_the_row_and_later_requests_still_run(tmp_path):
    env = make(tmp_path)
    env.sender.result = False
    await env.run(incoming(1), incoming(2))
    assert env.state.get_handled(ROOM, 1).state == "abandoned" and env.state.get_handled(ROOM, 2).state == "abandoned"
    assert len(env.submitter.submits) == 2 and env.pool.stats.send_failures == 2


async def test_K23_a_sender_exception_does_not_kill_the_room_worker(tmp_path):
    env = make(tmp_path)
    env.sender.error = RuntimeError("보내기 오류")
    await env.run(incoming(1))
    env.sender.error = None
    await env.run(incoming(2))
    assert env.state.get_handled(ROOM, 1).state == "abandoned" and env.state.get_handled(ROOM, 2).state == "replied"
    assert env.pool.stats.errors == 1


async def test_K24_an_unexpected_submitter_error_posts_an_internal_failure_and_continues(tmp_path):
    env = make(tmp_path)
    env.submitter.get_error = RuntimeError("어댑터 버그")
    await env.run(incoming(1))
    env.submitter.get_error = None
    await env.run(incoming(2))
    assert env.sender.texts()[0] == "[실패] (INTERNAL)" and env.sender.texts()[1] == "answer-for-r-0002"


async def test_K25_log_lines_never_contain_the_request_or_the_answer(tmp_path, caplog):
    env = make(tmp_path)
    env.submitter.outcome_fn = lambda rid, n: ChatOutcome("done", True, None, "비밀 답변 내용", None)
    with caplog.at_level(logging.DEBUG):
        await env.run(incoming(1, body="비밀 요청 내용"))
    assert "비밀 요청 내용" not in caplog.text and "비밀 답변 내용" not in caplog.text


# ── 재시작 복구 ───────────────────────────────────────────────────────────

async def test_K26_recover_posts_one_interrupted_notice_per_room_and_abandons_the_open_rows(tmp_path):
    env = make(tmp_path)
    for seq in (1, 2, 3):
        env.state.begin(ROOM, seq, "queued", now=NOW_ISO)
    env.state.set_state(ROOM, 2, "submitted", now=NOW_ISO, request_id="r-9")
    env.state.begin(ROOM2, 1, "queued", now=NOW_ISO)
    env.state.begin(ROOM, 4, "skipped", now=NOW_ISO)
    n = await env.pool.recover()
    assert n == 4
    assert sorted((r, t) for r, t, _ in env.sender.posts) == sorted([(ROOM, "[중단됨]"), (ROOM2, "[중단됨]")])
    assert {env.state.get_handled(ROOM, s).state for s in (1, 2, 3)} == {"abandoned"} and env.state.open_requests() == []
    assert env.state.get_handled(ROOM, 4).state == "skipped"
    ids = [c for _, _, c in env.sender.posts]
    assert f"interrupted:{ROOM}:3" in ids and f"interrupted:{ROOM2}:1" in ids


async def test_K27_recover_does_not_post_into_a_closed_room_but_still_abandons_its_rows(tmp_path):
    env = make(tmp_path)
    env.state.begin(ROOM, 1, "queued", now=NOW_ISO)
    env.state.close_room(ROOM, now=NOW_ISO)
    assert await env.pool.recover() == 1
    assert env.sender.posts == [] and env.state.get_handled(ROOM, 1).state == "abandoned"


async def test_K28_recover_with_nothing_open_does_nothing(tmp_path):
    env = make(tmp_path)
    assert await env.pool.recover() == 0 and env.sender.posts == []


async def test_K29_a_failed_interrupted_notice_still_abandons_the_rows(tmp_path):
    env = make(tmp_path)
    env.state.begin(ROOM, 1, "queued", now=NOW_ISO)
    env.sender.result = False
    assert await env.pool.recover() == 1
    assert env.state.get_handled(ROOM, 1).state == "abandoned"


# ── 문구 설정 ─────────────────────────────────────────────────────────────

def test_K30_reply_texts_require_every_key_with_a_non_empty_text():
    ReplyTexts(TEXTS, stop_labels={})
    for key in TEXTS:
        broken = {k: v for k, v in TEXTS.items() if k != key}
        with pytest.raises(ValueError):
            ReplyTexts(broken, stop_labels={})
    with pytest.raises(ValueError):
        ReplyTexts({**TEXTS, "failed": "  "}, stop_labels={})
