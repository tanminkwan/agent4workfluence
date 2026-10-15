"""채팅 상태 저장소 SqliteChatState (Phase 5 설계 4.3·6·11.1절, FR-C03·C04·C16, NFR-C04, AC-C1·C5).

실제 SQLite(임시 파일)를 쓴다. 방마다 처리한 마지막 seq와 메시지별 처리 상태를 둔다. 본문·표시 이름은 저장하지 않는다.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from a4w_inference.channels.chat.store import HandledRow, RoomState, SqliteChatState

ROOM, ROOM2 = "room-1", "room-2"
T0 = "2026-10-15T07:00:00Z"
T1 = "2026-10-15T07:05:00Z"
T2 = "2026-10-16T07:00:00Z"


def make(tmp_path):
    return SqliteChatState(tmp_path / "state" / "chat.db")


# ── 방 ────────────────────────────────────────────────────────────────────

def test_S1_a_new_room_starts_at_the_given_seq_and_is_open(tmp_path):
    s = make(tmp_path)
    assert s.get_room(ROOM) is None and s.last_seq(ROOM) is None
    assert s.register_room(ROOM, "direct", start_seq=12, now=T0) is True
    assert s.get_room(ROOM) == RoomState(room_id=ROOM, kind="direct", last_seq=12, joined_at=T0, closed_at=None)
    assert s.last_seq(ROOM) == 12


def test_S2_registering_a_known_room_again_changes_nothing(tmp_path):
    s = make(tmp_path)
    s.register_room(ROOM, "group", start_seq=5, now=T0)
    assert s.begin(ROOM, 6, "queued", now=T0) is True
    assert s.register_room(ROOM, "group", start_seq=99, now=T1) is False
    r = s.get_room(ROOM)
    assert r.last_seq == 6 and r.joined_at == T0


def test_S3_the_room_kind_is_updated_when_it_was_wrong_at_first_registration(tmp_path):
    s = make(tmp_path)
    s.register_room(ROOM, "unknown", start_seq=0, now=T0)
    s.register_room(ROOM, "group", start_seq=0, now=T1)
    assert s.get_room(ROOM).kind == "group"


def test_S4_open_rooms_excludes_closed_ones(tmp_path):
    s = make(tmp_path)
    s.register_room(ROOM, "direct", start_seq=0, now=T0)
    s.register_room(ROOM2, "group", start_seq=0, now=T0)
    s.close_room(ROOM, now=T1)
    assert [r.room_id for r in s.open_rooms()] == [ROOM2]
    assert s.get_room(ROOM).closed_at == T1


def test_S5_rejoining_a_closed_room_reopens_it_and_skips_what_happened_while_away(tmp_path):
    """나갔다 다시 초대된 방: 그 사이 메시지는 우리 몫이 아니다. 현재 seq에서 새로 시작한다(요구 10절 6번과 같은 규칙)."""
    s = make(tmp_path)
    s.register_room(ROOM, "group", start_seq=0, now=T0)
    s.begin(ROOM, 1, "queued", now=T0)
    s.close_room(ROOM, now=T1)
    assert s.register_room(ROOM, "group", start_seq=40, now=T2) is True
    r = s.get_room(ROOM)
    assert r.closed_at is None and r.last_seq == 40 and r.joined_at == T2


def test_S6_closing_an_unknown_room_is_a_no_op(tmp_path):
    make(tmp_path).close_room("nope", now=T0)


# ── 중복 제거 (begin) ─────────────────────────────────────────────────────

def test_D1_begin_claims_a_seq_once(tmp_path):
    s = make(tmp_path)
    s.register_room(ROOM, "direct", start_seq=0, now=T0)
    assert s.begin(ROOM, 1, "queued", now=T0) is True
    assert s.begin(ROOM, 1, "queued", now=T0) is False          # 같은 (roomId, seq)는 두 번 처리하지 않는다
    assert s.last_seq(ROOM) == 1


def test_D2_seqs_at_or_below_last_seq_are_dropped(tmp_path):
    s = make(tmp_path)
    s.register_room(ROOM, "direct", start_seq=10, now=T0)
    assert s.begin(ROOM, 10, "queued", now=T0) is False
    assert s.begin(ROOM, 3, "queued", now=T0) is False
    assert s.get_handled(ROOM, 3) is None
    assert s.begin(ROOM, 11, "queued", now=T0) is True


def test_D3_a_late_lower_seq_after_a_higher_one_is_dropped(tmp_path):
    s = make(tmp_path)
    s.register_room(ROOM, "direct", start_seq=0, now=T0)
    assert s.begin(ROOM, 5, "queued", now=T0) is True
    assert s.begin(ROOM, 4, "queued", now=T0) is False
    assert s.last_seq(ROOM) == 5


def test_D4_skipped_messages_also_advance_last_seq(tmp_path):
    """join·leave·요청이 아닌 메시지는 처리한 것으로 기록하고 다시 보지 않는다 (FR-C05)."""
    s = make(tmp_path)
    s.register_room(ROOM, "group", start_seq=0, now=T0)
    assert s.begin(ROOM, 1, "skipped", now=T0) is True
    assert s.last_seq(ROOM) == 1 and s.get_handled(ROOM, 1).state == "skipped"
    assert s.begin(ROOM, 1, "queued", now=T0) is False


def test_D5_rooms_are_independent(tmp_path):
    s = make(tmp_path)
    s.register_room(ROOM, "direct", start_seq=0, now=T0)
    s.register_room(ROOM2, "direct", start_seq=0, now=T0)
    assert s.begin(ROOM, 1, "queued", now=T0) and s.begin(ROOM2, 1, "queued", now=T0)
    assert s.last_seq(ROOM2) == 1


def test_D6_begin_on_an_unknown_room_or_bad_state_is_rejected(tmp_path):
    s = make(tmp_path)
    with pytest.raises(KeyError):
        s.begin("nope", 1, "queued", now=T0)
    s.register_room(ROOM, "direct", start_seq=0, now=T0)
    for bad in ("replied", "done", "", "QUEUED"):            # begin은 queued·skipped만 받는다
        with pytest.raises(ValueError):
            s.begin(ROOM, 1, bad, now=T0)
    assert s.last_seq(ROOM) == 0


def test_D7_begin_on_a_closed_room_is_dropped(tmp_path):
    s = make(tmp_path)
    s.register_room(ROOM, "direct", start_seq=0, now=T0)
    s.close_room(ROOM, now=T1)
    assert s.begin(ROOM, 1, "queued", now=T1) is False


def test_D8_concurrent_claims_of_the_same_seq_have_exactly_one_winner(tmp_path):
    """WS 프레임과 보충이 같은 메시지를 동시에 넣어도 한 번만 처리된다 (NFR-C04)."""
    s = make(tmp_path)
    s.register_room(ROOM, "direct", start_seq=0, now=T0)
    results, barrier = [], threading.Barrier(8)

    def claim():
        barrier.wait()
        results.append(s.begin(ROOM, 1, "queued", now=T0))

    threads = [threading.Thread(target=claim) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(True) == 1 and results.count(False) == 7


# ── 처리 상태 ─────────────────────────────────────────────────────────────

def test_H1_a_queued_message_moves_through_its_states(tmp_path):
    s = make(tmp_path)
    s.register_room(ROOM, "direct", start_seq=0, now=T0)
    s.begin(ROOM, 1, "queued", now=T0)
    assert s.get_handled(ROOM, 1) == HandledRow(room_id=ROOM, seq=1, state="queued", request_id=None, reply_msg_id=None,
                                                created_at=T0, updated_at=T0)
    s.set_state(ROOM, 1, "submitted", now=T1, request_id="r-0001")
    h = s.get_handled(ROOM, 1)
    assert h.state == "submitted" and h.request_id == "r-0001" and h.created_at == T0 and h.updated_at == T1
    s.set_state(ROOM, 1, "replied", now=T2, reply_msg_id="reply:r-0001")
    h = s.get_handled(ROOM, 1)
    assert h.state == "replied" and h.request_id == "r-0001" and h.reply_msg_id == "reply:r-0001"


def test_H2_set_state_rejects_unknown_states_and_unknown_rows(tmp_path):
    s = make(tmp_path)
    s.register_room(ROOM, "direct", start_seq=0, now=T0)
    s.begin(ROOM, 1, "queued", now=T0)
    with pytest.raises(ValueError):
        s.set_state(ROOM, 1, "weird", now=T1)
    with pytest.raises(KeyError):
        s.set_state(ROOM, 99, "submitted", now=T1)


def test_H3_open_requests_are_the_queued_and_submitted_ones_in_seq_order(tmp_path):
    s = make(tmp_path)
    s.register_room(ROOM, "group", start_seq=0, now=T0)
    s.register_room(ROOM2, "direct", start_seq=0, now=T0)
    for seq, state in [(1, "skipped"), (2, "queued"), (3, "queued"), (4, "queued")]:
        s.begin(ROOM, seq, state, now=T0)
    s.begin(ROOM2, 1, "queued", now=T0)
    s.set_state(ROOM, 3, "submitted", now=T1, request_id="r-3")
    s.set_state(ROOM, 4, "replied", now=T1)
    got = [(h.room_id, h.seq, h.state) for h in s.open_requests()]
    assert got == [(ROOM, 2, "queued"), (ROOM, 3, "submitted"), (ROOM2, 1, "queued")]


def test_H4_abandoning_an_open_request_removes_it_from_the_open_list(tmp_path):
    s = make(tmp_path)
    s.register_room(ROOM, "direct", start_seq=0, now=T0)
    s.begin(ROOM, 1, "queued", now=T0)
    s.set_state(ROOM, 1, "abandoned", now=T1)
    assert s.open_requests() == [] and s.get_handled(ROOM, 1).state == "abandoned"


# ── 재시작·보관 ───────────────────────────────────────────────────────────

def test_R1_state_survives_a_restart(tmp_path):
    s = make(tmp_path)
    s.register_room(ROOM, "direct", start_seq=0, now=T0)
    s.begin(ROOM, 1, "queued", now=T0)
    s.set_state(ROOM, 1, "submitted", now=T1, request_id="r-1")
    again = make(tmp_path)
    assert again.last_seq(ROOM) == 1
    assert again.begin(ROOM, 1, "queued", now=T2) is False
    assert [h.seq for h in again.open_requests()] == [1]


def test_R2_purge_removes_old_finished_rows_but_keeps_open_ones_and_rooms(tmp_path):
    s = make(tmp_path)
    s.register_room(ROOM, "group", start_seq=0, now=T0)
    s.begin(ROOM, 1, "skipped", now=T0)
    s.begin(ROOM, 2, "queued", now=T0)
    s.set_state(ROOM, 2, "replied", now=T0)
    s.begin(ROOM, 3, "queued", now=T0)                    # 열린 요청은 오래돼도 지우지 않는다
    s.begin(ROOM, 4, "queued", now=T2)
    s.set_state(ROOM, 4, "replied", now=T2)
    assert s.purge(before="2026-10-16T00:00:00Z") == 2
    assert s.get_handled(ROOM, 1) is None and s.get_handled(ROOM, 2) is None
    assert s.get_handled(ROOM, 3) is not None and s.get_handled(ROOM, 4) is not None
    assert s.get_room(ROOM) is not None


def test_R3_purging_handled_rows_does_not_reopen_old_seqs_for_processing(tmp_path):
    """행이 지워져도 last_seq가 남아 있어 같은 메시지를 다시 처리하지 않는다."""
    s = make(tmp_path)
    s.register_room(ROOM, "direct", start_seq=0, now=T0)
    s.begin(ROOM, 1, "queued", now=T0)
    s.set_state(ROOM, 1, "replied", now=T0)
    s.purge(before=T2)
    assert s.get_handled(ROOM, 1) is None
    assert s.begin(ROOM, 1, "queued", now=T2) is False


# ── 형식·비밀 ─────────────────────────────────────────────────────────────

def test_F1_the_schema_has_no_column_for_message_bodies_or_names(tmp_path):
    make(tmp_path)
    conn = sqlite3.connect(tmp_path / "state" / "chat.db")
    columns = {t: [r[1] for r in conn.execute(f"PRAGMA table_info({t})")] for t in ("rooms", "handled")}
    conn.close()
    assert columns["rooms"] == ["room_id", "kind", "last_seq", "joined_at", "closed_at"]
    assert columns["handled"] == ["room_id", "seq", "state", "request_id", "reply_msg_id", "created_at", "updated_at"]


def test_F2_a_second_instance_can_use_the_same_file_from_another_thread(tmp_path):
    s = make(tmp_path)
    s.register_room(ROOM, "direct", start_seq=0, now=T0)
    out = []
    t = threading.Thread(target=lambda: out.append(s.begin(ROOM, 1, "queued", now=T0)))
    t.start()
    t.join()
    assert out == [True] and make(tmp_path).last_seq(ROOM) == 1
