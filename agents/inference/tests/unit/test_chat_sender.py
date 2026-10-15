"""ChatSender: 방에 글 보내기 (Phase 5 설계 5.2절, FR-C21·C22·C24·C25·C27, AC-C6·C7).

평문화 → 길이 규칙 → 속도 제한 → 보내기(재시도) 순서다. 가짜 채팅 API와 가짜 limiter·sleep을 쓴다.
같은 clientMsgId로 다시 보내도 서버가 한 번만 반영하므로 429·5xx·네트워크 오류는 재시도하고, 그 밖의 4xx는 재시도하지 않는다.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from a4w_inference.channels.chat.ports import ChatApiError, ReplySender, SendResult
from a4w_inference.channels.chat.sender import ChatSender
from tests.chat_fakes import ROOM, FakeChatApi

pytestmark = pytest.mark.anyio
NOTICE = "나머지는 올리지 않았습니다. 필요하면 페이지에 올리라고 지시해 주세요."


class FakeLimiter:
    def __init__(self):
        self.acquired = 0

    async def acquire(self) -> None:
        self.acquired += 1


class ScriptedApi(FakeChatApi):
    """send가 던질 오류를 앞에서부터 차례로 정한다(None은 성공)."""

    def __init__(self, script=()):
        super().__init__()
        self.script = list(script)

    async def send(self, room_id, body, *, client_msg_id=None):
        self.sent.append((room_id, body, client_msg_id))
        step = self.script.pop(0) if self.script else None
        if isinstance(step, Exception):
            raise step
        return SendResult(message_id=f"m-{len(self.sent)}", duplicate=bool(step == "dup"))


def make(script=(), **kw):
    api, limiter, sleeps = ScriptedApi(script), FakeLimiter(), []

    async def sleep(seconds):
        sleeps.append(seconds)
        await asyncio.sleep(0)

    opts = dict(limiter=limiter, answer_max_chars=100, hard_max_chars=300, truncate_notice=NOTICE, retry_max=2, retry_seconds=5.0,
                retry_wait_max_seconds=60.0, sleep=sleep)
    opts.update(kw)
    return ChatSender(api, **opts), api, limiter, sleeps


def err(status, code, retry=None):
    return ChatApiError(code, "m", status=status, retry_after_seconds=retry)


# ── 본문 만들기 ───────────────────────────────────────────────────────────

async def test_E1_post_sends_the_text_with_the_client_msg_id_and_returns_true():
    s, api, limiter, _ = make()
    assert isinstance(s, ReplySender)
    assert await s.post(ROOM, "회의록을 찾았습니다", client_msg_id="reply:r-1") is True
    assert api.sent == [(ROOM, "회의록을 찾았습니다", "reply:r-1")] and limiter.acquired == 1


async def test_E2_markdown_is_stripped_before_sending():
    s, api, *_ = make()
    await s.post(ROOM, "# 결과\n**3개**를 찾았습니다.\n* [회의록](https://x/1)", client_msg_id="c")
    assert api.sent[0][1] == "결과\n3개를 찾았습니다.\n- 회의록 (https://x/1)"


async def test_E3_a_long_answer_is_cut_and_the_notice_is_appended_within_the_hard_limit():
    s, api, *_ = make(answer_max_chars=40, hard_max_chars=200)
    text = "\n".join(f"{i}번째 줄입니다" for i in range(50))
    assert await s.post(ROOM, text, client_msg_id="c") is True
    body = api.sent[0][1]
    assert body.endswith(NOTICE) and len(body) <= 200 and body.rsplit("\n", 1)[0] in text


async def test_E4_a_blank_text_is_not_sent_and_returns_false():
    s, api, limiter, _ = make()
    for blank in ("", "   ", "\n\n", "**", "> "):
        assert await s.post(ROOM, blank, client_msg_id="c") is False
    assert api.sent == [] and limiter.acquired == 0


async def test_E5_a_duplicate_response_counts_as_sent():
    s, api, *_ = make(["dup"])
    assert await s.post(ROOM, "답", client_msg_id="c") is True


async def test_E6_a_long_client_msg_id_is_shortened_deterministically_to_64_chars():
    s, api, *_ = make()
    long_a, long_b = "reply:" + "a" * 80, "reply:" + "b" * 80
    await s.post(ROOM, "답", client_msg_id=long_a)
    await s.post(ROOM, "답", client_msg_id=long_a)
    await s.post(ROOM, "답", client_msg_id=long_b)
    ids = [c for _, _, c in api.sent]
    assert all(1 <= len(i) <= 64 for i in ids) and ids[0] == ids[1] and ids[0] != ids[2]


async def test_E7_a_short_client_msg_id_is_sent_unchanged():
    s, api, *_ = make()
    await s.post(ROOM, "답", client_msg_id="x" * 64)
    assert api.sent[0][2] == "x" * 64


# ── 재시도 ────────────────────────────────────────────────────────────────

async def test_E8_rate_limited_waits_for_retry_after_and_resends_with_the_same_id():
    s, api, limiter, sleeps = make([err(429, "RATE_LIMITED", 3.0)])
    assert await s.post(ROOM, "답", client_msg_id="reply:r-1") is True
    assert sleeps == [3.0] and [c for _, _, c in api.sent] == ["reply:r-1", "reply:r-1"] and limiter.acquired == 2


async def test_E9_rate_limited_without_retry_after_uses_the_configured_wait():
    s, api, _, sleeps = make([err(429, "RATE_LIMITED")], retry_seconds=7.0)
    assert await s.post(ROOM, "답", client_msg_id="c") is True and sleeps == [7.0]


async def test_E10_a_huge_retry_after_is_capped():
    s, api, _, sleeps = make([err(429, "RATE_LIMITED", 3600.0)], retry_wait_max_seconds=30.0)
    await s.post(ROOM, "답", client_msg_id="c")
    assert sleeps == [30.0]


@pytest.mark.parametrize("failure", [err(503, "UNAVAILABLE"), err(500, "INTERNAL"), ChatApiError("NETWORK", "끊김")])
async def test_E11_server_errors_and_network_failures_are_retried(failure):
    s, api, _, sleeps = make([failure, failure], retry_max=2, retry_seconds=4.0)
    assert await s.post(ROOM, "답", client_msg_id="c") is True
    assert len(api.sent) == 3 and sleeps == [4.0, 4.0]


async def test_E12_retries_stop_after_retry_max_and_the_result_is_false():
    f = err(503, "UNAVAILABLE")
    s, api, limiter, sleeps = make([f, f, f, f], retry_max=2)
    assert await s.post(ROOM, "답", client_msg_id="c") is False
    assert len(api.sent) == 3 and len(sleeps) == 2 and limiter.acquired == 3


@pytest.mark.parametrize("failure", [err(400, "TOO_LONG"), err(400, "EMPTY"), err(400, "BAD_CHAR"), err(403, "INSUFFICIENT_SCOPE"),
                                     err(403, "FORBIDDEN"), err(404, "NOT_FOUND"), err(401, "UNAUTHORIZED")])
async def test_E13_other_client_errors_are_not_retried(failure):
    s, api, _, sleeps = make([failure])
    assert await s.post(ROOM, "답", client_msg_id="c") is False
    assert len(api.sent) == 1 and sleeps == []


async def test_E14_an_unexpected_exception_is_not_swallowed():
    s, *_ = make([RuntimeError("버그")])
    with pytest.raises(RuntimeError):
        await s.post(ROOM, "답", client_msg_id="c")


async def test_E15_cancellation_while_waiting_propagates():
    gate = asyncio.Event()

    async def hang(_):
        await gate.wait()

    s, *_ = make([err(429, "RATE_LIMITED", 5.0)], sleep=hang)
    task = asyncio.create_task(s.post(ROOM, "답", client_msg_id="c"))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ── 설정·로그 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kw", [dict(answer_max_chars=290, hard_max_chars=300), dict(answer_max_chars=0), dict(truncate_notice=""),
                                dict(retry_max=-1), dict(retry_seconds=-1.0), dict(retry_wait_max_seconds=0.0)])
def test_E16_invalid_settings_fail_at_construction(kw):
    with pytest.raises(ValueError):
        make(**kw)


async def test_E17_logs_never_contain_the_message_text(caplog):
    s, *_ = make([err(400, "TOO_LONG")])
    with caplog.at_level(logging.DEBUG):
        await s.post(ROOM, "비밀 답변 내용", client_msg_id="c")
    assert "비밀 답변 내용" not in caplog.text
