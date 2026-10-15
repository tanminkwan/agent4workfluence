"""ReplyPolicy: 어떤 메시지가 요청인가, 쓰기를 허용하는가 (Phase 5 설계 4.2·8절, FR-C05·C11·C12·C32·C33, AC-C2·C3·C8).

순수 판단이다. 입출력은 값 객체뿐이고 네트워크·저장소를 모른다.
"""

from __future__ import annotations

import pytest

from a4w_inference.channels.chat.models import ChatMessage, Member, RoomInfo
from a4w_inference.channels.chat.policy import ReplyPolicy, requester_label

ME = "c4838c0e-f59b-42ab-9381-6ca531d29e14"
ALICE = "ba6387f9-6d24-4a9f-8dc5-b57b57f1cb38"
BOB = "d15736fd-d217-4f85-9219-bfb6435143ac"
ROOM = "bfe0ce49-02f4-40da-b46c-a9c8f84deb25"
NAME = "AI Agent"


def room(kind="group", members=None):
    members = members if members is not None else (Member(ME, NAME), Member(ALICE, "tiffanie"), Member(BOB, "tanminkwan"))
    return RoomInfo(room_id=ROOM, kind=kind, name="시험 방", members=tuple(members))


def message(body="회의록 찾아줘", *, sender=ALICE, kind="user", seq=3):
    return ChatMessage(room_id=ROOM, seq=seq, sender_id=sender, body=body, kind=kind, created_at="2026-10-15T06:14:07.882Z")


def policy(**kw):
    base = dict(mention_name=NAME, write_allowed_users=(ALICE,))
    base.update(kw)
    return ReplyPolicy(**base)


# ── 요청이 아닌 것 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind", ["join", "leave", "system", ""])
def test_P1_only_kind_user_is_considered(kind):
    d = policy().decide(message(kind=kind), room("direct"), my_user_id=ME)
    assert d.is_request is False and d.reason == "not_user_kind" and d.allow_writes is False


def test_P2_my_own_message_is_never_a_request_even_in_a_direct_room():
    """내가 보낸 메시지도 돌아온다. 답하면 루프가 된다 (FR-C05)."""
    d = policy(write_allowed_users=(ME,)).decide(message(sender=ME), room("direct"), my_user_id=ME)
    assert d.is_request is False and d.reason == "own_message" and d.allow_writes is False


def test_P3_my_own_message_is_checked_before_the_room_kind():
    d = policy().decide(message(f"{NAME} 안녕", sender=ME), room("group"), my_user_id=ME)
    assert d.reason == "own_message"


def test_P4_unknown_room_or_unknown_kind_is_ignored_as_safe_default():
    assert policy().decide(message(), None, my_user_id=ME).reason == "unknown_room"
    d = policy().decide(message(f"{NAME} 도와줘"), room("channel"), my_user_id=ME)
    assert d.is_request is False and d.reason == "unknown_room_kind"


# ── 1:1 · 여럿 ────────────────────────────────────────────────────────────

def test_P5_direct_room_treats_every_user_message_as_a_request():
    d = policy().decide(message("그냥 말하기"), room("direct"), my_user_id=ME)
    assert d.is_request is True and d.reason == "direct"


@pytest.mark.parametrize("body", [
    "AI Agent 회의록 찾아줘", "회의록 찾아줘 AI Agent", "@AI Agent 회의록 찾아줘", "ai agent 도와줘", "  AI   Agent   도와줘",
    "안녕하세요, AI Agent님", "AI Agent 도와줘",
])
def test_P6_group_room_replies_when_the_display_name_is_in_the_body(body):
    d = policy().decide(message(body), room("group"), my_user_id=ME)
    assert d.is_request is True and d.reason == "mentioned"


@pytest.mark.parametrize("body", ["회의록 찾아줘", "AI 에이전트", "Agent AI", "AIAgent", "", "tiffanie 오늘 회의 있어?"])
def test_P7_group_room_ignores_messages_without_the_display_name(body):
    d = policy().decide(message(body), room("group"), my_user_id=ME)
    assert d.is_request is False and d.reason == "not_mentioned" and d.allow_writes is False


def test_P8_mention_name_is_a_plain_substring_match_not_a_regex():
    d = policy(mention_name="A.I (봇)").decide(message("A.I (봇) 도와줘"), room("group"), my_user_id=ME)
    assert d.is_request is True
    assert policy(mention_name="A.I (봇)").decide(message("AxI 봇 도와줘"), room("group"), my_user_id=ME).is_request is False


def test_P9_empty_mention_name_is_a_config_error():
    for bad in ("", "   "):
        with pytest.raises(ValueError):
            ReplyPolicy(mention_name=bad, write_allowed_users=())


# ── 쓰기 허용 (미정 18) ───────────────────────────────────────────────────

def test_P10_writes_are_allowed_only_for_listed_sender_ids():
    p = policy(write_allowed_users=(ALICE,))
    assert p.decide(message(sender=ALICE), room("direct"), my_user_id=ME).allow_writes is True
    d = p.decide(message(sender=BOB), room("direct"), my_user_id=ME)
    assert d.is_request is True and d.allow_writes is False


def test_P11_empty_allow_list_means_nobody_may_write():
    d = policy(write_allowed_users=()).decide(message(sender=ALICE), room("direct"), my_user_id=ME)
    assert d.is_request is True and d.allow_writes is False


def test_P12_the_decision_uses_sender_id_not_the_display_name():
    """이름이 같은 다른 사람이 쓰기를 얻으면 안 된다 (FR-C33)."""
    impostor = Member(BOB, "tiffanie")
    r = room("direct", members=(Member(ME, NAME), Member(ALICE, "other"), impostor))
    d = policy(write_allowed_users=("tiffanie",)).decide(message(sender=BOB), r, my_user_id=ME)
    assert d.allow_writes is False


def test_P13_allow_list_entries_are_matched_exactly_without_case_folding():
    d = policy(write_allowed_users=(ALICE.upper(),)).decide(message(sender=ALICE), room("direct"), my_user_id=ME)
    assert d.allow_writes is False


def test_P14_allow_list_accepts_any_iterable_and_is_copied():
    allowed = [ALICE]
    p = policy(write_allowed_users=allowed)
    allowed.append(BOB)
    assert p.decide(message(sender=BOB), room("direct"), my_user_id=ME).allow_writes is False


def test_P15_a_non_request_never_allows_writes_even_for_a_listed_sender():
    d = policy().decide(message("그냥 말"), room("group"), my_user_id=ME)
    assert d.is_request is False and d.allow_writes is False


# ── 요청자 이름 ───────────────────────────────────────────────────────────

def test_P16_requester_label_is_the_member_display_name():
    assert requester_label(ALICE, room()) == "tiffanie"


def test_P17_requester_label_falls_back_to_the_sender_id():
    assert requester_label("e0000000-0000-0000-0000-000000000000", room()) == "e0000000-0000-0000-0000-000000000000"
    assert requester_label(ALICE, None) == ALICE
    assert requester_label(ALICE, room(members=(Member(ALICE, ""),))) == ALICE


def test_P18_decision_is_immutable():
    d = policy().decide(message(), room("direct"), my_user_id=ME)
    with pytest.raises(Exception):
        d.is_request = False  # type: ignore[misc]
