"""ChatDispatcher: 프레임·보충 → 중복 제거 → 요청 판단 → 요청 전달 (Phase 5 설계 3·4.2·4.3절, FR-C03~C05·C13, AC-C1·C2·C3).

가짜 채팅 API와 실제 SQLite 상태, 실제 ReplyPolicy를 쓴다. 요청은 sink로 나간다(수행 연결은 S5).
프레임은 감독이 한 번에 하나씩 넘기므로 hello 처리(보충)가 끝나야 다음 실시간 프레임이 처리된다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from a4w_inference.channels.chat.dispatcher import ChatDispatcher
from a4w_inference.channels.chat.models import HelloFrame, IncomingRequest, MessageFrame, RoomFrame
from a4w_inference.channels.chat.policy import ReplyPolicy
from a4w_inference.channels.chat.ports import ChatApiError
from a4w_inference.channels.chat.store import SqliteChatState
from tests.chat_fakes import (ALICE, BOB, ME, NOW_ISO, OLD, RECENT, ROOM, ROOM2, FakeChatApi, chat_message, members, room_info)

pytestmark = pytest.mark.anyio


class Sink:
    def __init__(self):
        self.got: list[IncomingRequest] = []
        self.fail_on: set[int] = set()

    async def __call__(self, request: IncomingRequest) -> None:
        if request.message.seq in self.fail_on:
            raise RuntimeError("sink failed")
        self.got.append(request)

    def seqs(self, room=ROOM):
        return [r.message.seq for r in self.got if r.room.room_id == room]


def make(tmp_path, api, *, page=200, writers=(ALICE,), max_age=86400):
    state = SqliteChatState(tmp_path / "chat.db")
    sink = Sink()
    clock = lambda: datetime(2026, 10, 15, 7, 0, tzinfo=UTC)
    d = ChatDispatcher(api=api, state=state, policy=ReplyPolicy(mention_name="AI Agent", write_allowed_users=writers), sink=sink,
                       clock=clock, now_iso=lambda: NOW_ISO, catchup_page=page, catchup_max_age_seconds=max_age)
    return d, state, sink


def hello(*room_ids):
    return HelloFrame(user_id=ME, room_ids=tuple(room_ids))


def live(seq, body="회의록 찾아줘", **kw):
    return MessageFrame(chat_message(seq, body, **kw))


# ── 처음 보는 방 ──────────────────────────────────────────────────────────

async def test_X1_a_room_seen_for_the_first_time_starts_at_its_current_seq_without_catch_up(tmp_path):
    """초대 전 대화에는 답하지 않는다 (요구 10절 6번)."""
    api = FakeChatApi([room_info(last_seq=7)], [chat_message(s) for s in range(1, 8)])
    d, state, sink = make(tmp_path, api)
    await d.handle(hello(ROOM))
    assert state.get_room(ROOM).last_seq == 7 and state.get_room(ROOM).kind == "direct"
    assert sink.got == [] and api.calls_of("messages") == []


async def test_X2_messages_after_the_starting_seq_are_requests(tmp_path):
    api = FakeChatApi([room_info(last_seq=7)])
    d, state, sink = make(tmp_path, api)
    await d.handle(hello(ROOM))
    await d.handle(live(8))
    assert sink.seqs() == [8] and state.last_seq(ROOM) == 8


# ── 보충 ──────────────────────────────────────────────────────────────────

async def test_X3_catch_up_fetches_after_the_last_seq_and_delivers_in_order(tmp_path):
    api = FakeChatApi([room_info(last_seq=6)], [chat_message(s) for s in range(1, 7)])
    d, state, sink = make(tmp_path, api)
    state.register_room(ROOM, "direct", start_seq=3, now=NOW_ISO)
    await d.handle(hello(ROOM))
    assert api.calls_of("messages") == [("messages", ROOM, 3, 200)]
    assert sink.seqs() == [4, 5, 6] and state.last_seq(ROOM) == 6


async def test_X4_a_full_page_means_keep_going_with_the_last_seq(tmp_path):
    api = FakeChatApi([room_info(last_seq=450)], [chat_message(s) for s in range(1, 451)])
    d, state, sink = make(tmp_path, api)
    state.register_room(ROOM, "direct", start_seq=0, now=NOW_ISO)
    await d.handle(hello(ROOM))
    assert [c[2:] for c in api.calls_of("messages")] == [(0, 200), (200, 200), (400, 200)]
    assert sink.seqs() == list(range(1, 451)) and state.last_seq(ROOM) == 450


async def test_X5_an_exactly_full_last_page_costs_one_more_empty_call(tmp_path):
    api = FakeChatApi([room_info(last_seq=6)], [chat_message(s) for s in range(1, 7)])
    d, state, sink = make(tmp_path, api, page=3)
    state.register_room(ROOM, "direct", start_seq=0, now=NOW_ISO)
    await d.handle(hello(ROOM))
    assert [c[2] for c in api.calls_of("messages")] == [0, 3, 6]
    assert sink.seqs() == [1, 2, 3, 4, 5, 6]


async def test_X6_a_live_frame_that_catch_up_already_delivered_is_not_delivered_again(tmp_path):
    api = FakeChatApi([room_info(last_seq=6)], [chat_message(s) for s in range(4, 7)])
    d, state, sink = make(tmp_path, api)
    state.register_room(ROOM, "direct", start_seq=3, now=NOW_ISO)
    await d.handle(hello(ROOM))
    await d.handle(live(6))
    await d.handle(live(7))
    assert sink.seqs() == [4, 5, 6, 7]


async def test_X7_a_second_hello_after_a_reconnect_catches_up_what_was_missed(tmp_path):
    api = FakeChatApi([room_info(last_seq=2)], [chat_message(1), chat_message(2)])
    d, state, sink = make(tmp_path, api)
    state.register_room(ROOM, "direct", start_seq=0, now=NOW_ISO)
    await d.handle(hello(ROOM))
    api.stored += [chat_message(3), chat_message(4)]           # 끊긴 사이 온 메시지
    await d.handle(hello(ROOM))
    assert sink.seqs() == [1, 2, 3, 4]


async def test_X8_catch_up_covers_every_open_room_and_skips_rooms_i_have_left(tmp_path):
    api = FakeChatApi([room_info(ROOM, last_seq=2), room_info(ROOM2, kind="group", last_seq=1)],
                      [chat_message(1), chat_message(2), chat_message(1, "AI Agent 안녕", room_id=ROOM2)])
    d, state, sink = make(tmp_path, api)
    state.register_room(ROOM, "direct", start_seq=0, now=NOW_ISO)
    state.register_room(ROOM2, "group", start_seq=0, now=NOW_ISO)
    state.register_room("gone-room", "direct", start_seq=0, now=NOW_ISO)         # 오프라인 동안 나간 방
    await d.handle(hello(ROOM, ROOM2))
    assert sink.seqs(ROOM) == [1, 2] and sink.seqs(ROOM2) == [1]
    assert state.get_room("gone-room").closed_at == NOW_ISO
    assert ("messages", "gone-room", 0, 200) not in api.calls


async def test_X9_one_room_failing_to_catch_up_does_not_stop_the_others(tmp_path):
    api = FakeChatApi([room_info(ROOM, last_seq=2), room_info(ROOM2, last_seq=2)],
                      [chat_message(1), chat_message(2), chat_message(1, room_id=ROOM2), chat_message(2, room_id=ROOM2)])
    api.fail_messages[ROOM] = ChatApiError("NETWORK", "끊김")
    d, state, sink = make(tmp_path, api)
    for r in (ROOM, ROOM2):
        state.register_room(r, "direct", start_seq=0, now=NOW_ISO)
    await d.handle(hello(ROOM, ROOM2))
    assert sink.seqs(ROOM) == [] and sink.seqs(ROOM2) == [1, 2]
    assert state.last_seq(ROOM) == 0 and d.stats.catchup_failures == 1             # 다음 hello에서 다시 시도한다
    del api.fail_messages[ROOM]
    await d.handle(hello(ROOM, ROOM2))
    assert sink.seqs(ROOM) == [1, 2]


async def test_X10_failing_to_list_rooms_propagates_so_the_supervisor_reconnects(tmp_path):
    api = FakeChatApi([room_info()])
    api.fail_rooms = ChatApiError("NETWORK", "끊김")
    d, _, _ = make(tmp_path, api)
    with pytest.raises(ChatApiError):
        await d.handle(hello(ROOM))


# ── 요청 판단 ─────────────────────────────────────────────────────────────

async def test_X11_non_user_kinds_and_my_own_messages_are_skipped_but_advance_last_seq(tmp_path):
    api = FakeChatApi([room_info(last_seq=0)])
    d, state, sink = make(tmp_path, api)
    await d.handle(hello(ROOM))
    await d.handle(live(1, "OO님이 들어왔습니다", kind="join", sender=BOB))
    await d.handle(live(2, "내가 한 말", sender=ME))
    await d.handle(live(3, "실제 요청"))
    assert sink.seqs() == [3] and state.last_seq(ROOM) == 3
    assert state.get_handled(ROOM, 1).state == "skipped" and state.get_handled(ROOM, 2).state == "skipped"
    assert state.get_handled(ROOM, 3).state == "queued"


async def test_X12_a_group_room_message_without_the_display_name_is_skipped(tmp_path):
    api = FakeChatApi([room_info(kind="group", last_seq=0)])
    d, state, sink = make(tmp_path, api)
    await d.handle(hello(ROOM))
    await d.handle(live(1, "점심 뭐 먹지"))
    await d.handle(live(2, "AI Agent 회의록 찾아줘"))
    assert sink.seqs() == [2] and state.get_handled(ROOM, 1).state == "skipped"


async def test_X13_the_request_carries_room_message_requester_and_write_permission(tmp_path):
    api = FakeChatApi([room_info(last_seq=0)])
    d, _, sink = make(tmp_path, api, writers=(ALICE,))
    await d.handle(hello(ROOM))
    await d.handle(live(1, "페이지 만들어줘", sender=ALICE))
    await d.handle(live(2, "페이지 만들어줘", sender=BOB))
    a, b = sink.got
    assert a.requester == "tiffanie" and a.allow_writes is True and a.message.body == "페이지 만들어줘" and a.room.room_id == ROOM
    assert b.requester == "tanminkwan" and b.allow_writes is False


async def test_X14_catch_up_messages_older_than_the_max_age_are_skipped_not_executed(tmp_path):
    """오래 꺼져 있다 켜졌을 때 낡은 요청이 한꺼번에 수행되는 것을 막는다 (설계 4.3)."""
    api = FakeChatApi([room_info(last_seq=3)], [chat_message(1, created_at=OLD), chat_message(2, created_at=OLD), chat_message(3, created_at=RECENT)])
    d, state, sink = make(tmp_path, api, max_age=86400)
    state.register_room(ROOM, "direct", start_seq=0, now=NOW_ISO)
    await d.handle(hello(ROOM))
    assert sink.seqs() == [3]
    assert state.get_handled(ROOM, 1).state == "skipped" and d.stats.stale_skipped == 2


async def test_X15_the_age_limit_does_not_apply_to_live_frames(tmp_path):
    api = FakeChatApi([room_info(last_seq=0)])
    d, _, sink = make(tmp_path, api)
    await d.handle(hello(ROOM))
    await d.handle(live(1, created_at=OLD))                  # 서버 시계 차이 등. 실시간으로 온 것은 처리한다
    assert sink.seqs() == [1]


async def test_X16_an_unparseable_created_at_in_catch_up_is_treated_as_stale(tmp_path):
    api = FakeChatApi([room_info(last_seq=1)], [chat_message(1, created_at="어제쯤")])
    d, state, sink = make(tmp_path, api)
    state.register_room(ROOM, "direct", start_seq=0, now=NOW_ISO)
    await d.handle(hello(ROOM))
    assert sink.got == [] and state.get_handled(ROOM, 1).state == "skipped"


# ── 방 변화 ───────────────────────────────────────────────────────────────

async def test_X17_room_joined_registers_the_room_at_its_current_seq_and_caches_members(tmp_path):
    api = FakeChatApi([room_info(ROOM2, kind="group", last_seq=5)])
    d, state, sink = make(tmp_path, api)
    await d.handle(hello())
    await d.handle(RoomFrame(ROOM2, "joined"))
    assert state.get_room(ROOM2).last_seq == 5 and state.get_room(ROOM2).kind == "group"
    await d.handle(live(6, "AI Agent 도와줘", room_id=ROOM2))
    assert sink.seqs(ROOM2) == [6] and sink.got[0].requester == "tiffanie"


async def test_X18_room_members_change_refreshes_the_member_names(tmp_path):
    api = FakeChatApi([room_info(last_seq=0)])
    d, _, sink = make(tmp_path, api)
    await d.handle(hello(ROOM))
    await d.handle(live(1, sender=BOB))
    api.room_list[ROOM] = room_info(last_seq=1, member_list=members((ME, "AI Agent"), (BOB, "새 이름")))
    await d.handle(RoomFrame(ROOM, "members"))
    await d.handle(live(2, sender=BOB))
    assert [r.requester for r in sink.got] == ["tanminkwan", "새 이름"]


async def test_X19_an_unknown_room_change_is_ignored_and_counted(tmp_path):
    api = FakeChatApi([room_info(last_seq=0)])
    d, _, _ = make(tmp_path, api)
    await d.handle(hello(ROOM))
    before = len(api.calls)
    await d.handle(RoomFrame(ROOM, "somethingNew"))
    assert len(api.calls) == before and d.stats.unknown_room_changes == 1


async def test_X20_a_message_for_a_room_i_do_not_know_registers_it_and_is_processed(tmp_path):
    """room 프레임보다 message가 먼저 올 수 있다. 이 메시지부터 우리 몫이다."""
    api = FakeChatApi([])
    d, state, sink = make(tmp_path, api)
    await d.handle(hello())
    api.room_list[ROOM2] = room_info(ROOM2, last_seq=9)       # hello 뒤에 생긴 방. room 프레임 없이 message가 먼저 온다
    await d.handle(live(9, room_id=ROOM2))
    assert sink.seqs(ROOM2) == [9] and state.last_seq(ROOM2) == 9


async def test_X21_my_own_leave_line_closes_the_room_and_later_messages_are_dropped(tmp_path):
    api = FakeChatApi([room_info(last_seq=0)])
    d, state, sink = make(tmp_path, api)
    await d.handle(hello(ROOM))
    await d.handle(live(1, "AI Agent님이 채팅방을 나갔습니다", kind="leave", sender=ME))
    await d.handle(live(2))
    assert state.get_room(ROOM).closed_at == NOW_ISO and sink.got == []


async def test_X22_someone_elses_leave_line_does_not_close_the_room(tmp_path):
    api = FakeChatApi([room_info(last_seq=0)])
    d, state, sink = make(tmp_path, api)
    await d.handle(hello(ROOM))
    await d.handle(live(1, "tiffanie님이 채팅방을 나갔습니다", kind="leave", sender=ALICE))
    await d.handle(live(2))
    assert state.get_room(ROOM).closed_at is None and sink.seqs() == [2]


# ── 오류 ──────────────────────────────────────────────────────────────────

async def test_X23_a_message_before_hello_is_dropped_and_picked_up_by_the_catch_up(tmp_path):
    """내 id를 모르면 자기 메시지를 가릴 수 없다. 처리하지 않고(seq도 안 올리고) hello 뒤 보충에 맡긴다."""
    api = FakeChatApi([room_info(last_seq=1)], [chat_message(1)])
    d, state, sink = make(tmp_path, api)
    state.register_room(ROOM, "direct", start_seq=0, now=NOW_ISO)
    await d.handle(live(1))
    assert sink.got == [] and state.last_seq(ROOM) == 0
    await d.handle(hello(ROOM))
    assert sink.seqs() == [1]


async def test_X24_a_sink_failure_leaves_the_row_queued_and_does_not_stop_later_messages(tmp_path):
    api = FakeChatApi([room_info(last_seq=0)])
    d, state, sink = make(tmp_path, api)
    sink.fail_on = {1}
    await d.handle(hello(ROOM))
    await d.handle(live(1))
    await d.handle(live(2))
    assert sink.seqs() == [2] and d.stats.sink_errors == 1
    assert state.get_handled(ROOM, 1).state == "queued" and state.last_seq(ROOM) == 2


async def test_X25_a_bad_catch_up_page_item_does_not_break_the_page(tmp_path):
    """API 계층이 깨진 항목을 버린다. 그래도 남은 메시지는 순서대로 처리한다."""
    api = FakeChatApi([room_info(last_seq=3)], [chat_message(1), chat_message(3)])
    d, state, sink = make(tmp_path, api)
    state.register_room(ROOM, "direct", start_seq=0, now=NOW_ISO)
    await d.handle(hello(ROOM))
    assert sink.seqs() == [1, 3] and state.last_seq(ROOM) == 3


async def test_X26_rooms_not_registered_but_listed_by_the_api_are_started_at_their_current_seq(tmp_path):
    api = FakeChatApi([room_info(ROOM, last_seq=4), room_info(ROOM2, kind="group", last_seq=9)])
    d, state, sink = make(tmp_path, api)
    await d.handle(hello(ROOM, ROOM2))
    assert state.last_seq(ROOM) == 4 and state.last_seq(ROOM2) == 9 and sink.got == []
