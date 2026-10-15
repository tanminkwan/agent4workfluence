"""pastMembers: 방을 나간 사람의 이름 (Phase 5 설계 4.3·7절 보강, workf 확인 2026-10-15).

방 정보에 members(지금 있는 사람)와 pastMembers(나간 사람)가 온다. 나간 사람이 쓴 메시지의 senderId는 members에 없으므로
요청자 이름은 members → pastMembers → senderId 순으로 찾는다. 다시 들어온 사람은 members에만 있다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from a4w_inference.channels.chat.dispatcher import ChatDispatcher
from a4w_inference.channels.chat.models import HelloFrame, Member, RoomInfo
from a4w_inference.channels.chat.policy import ReplyPolicy, requester_label
from a4w_inference.channels.chat.rest_api import WorkfluenceChatApi
from a4w_inference.channels.chat.store import SqliteChatState
from tests.chat_fakes import ALICE, BOB, ME, NOW_ISO, ROOM, FakeChatApi, chat_message

pytestmark = pytest.mark.anyio
GONE = "e0000000-0000-0000-0000-00000000000e"


def room(members=(), past=(), kind="direct"):
    return RoomInfo(room_id=ROOM, kind=kind, name="방", members=tuple(members), past_members=tuple(past), last_seq=0)


def test_M1_room_info_has_no_past_members_by_default():
    assert RoomInfo(room_id=ROOM, kind="direct", name="").past_members == ()


def test_M2_requester_label_prefers_current_members_then_past_members_then_the_id():
    r = room(members=(Member(ALICE, "tiffanie"),), past=(Member(GONE, "나간 사람 이름"), Member(ALICE, "옛 이름")))
    assert requester_label(ALICE, r) == "tiffanie"                      # 다시 들어온 사람은 members가 이긴다
    assert requester_label(GONE, r) == "나간 사람 이름"
    assert requester_label("e1111111-0000-0000-0000-000000000000", r) == "e1111111-0000-0000-0000-000000000000"


def test_M3_a_blank_past_member_name_falls_back_to_the_id():
    assert requester_label(GONE, room(past=(Member(GONE, "  "),))) == GONE


def api_with(handler):
    return WorkfluenceChatApi("https://wf.test/api/v1", "tok", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def room_json(**extra):
    d = {"id": ROOM, "kind": "group", "name": "방", "createdBy": ALICE, "lastSeq": 3, "unread": 0,
         "members": [{"userId": ME, "displayName": "AI Agent"}], "pastMembers": [{"userId": GONE, "displayName": "나간 사람 이름"}]}
    d.update(extra)
    return d


async def test_M4_rest_rooms_and_room_parse_past_members():
    api = api_with(lambda r: httpx.Response(200, json={"items": [room_json()]} if r.url.path.endswith("/rooms") else room_json()))
    (listed,) = await api.rooms()
    one = await api.room(ROOM)
    for r in (listed, one):
        assert r.members == (Member(ME, "AI Agent"),) and r.past_members == (Member(GONE, "나간 사람 이름"),)


@pytest.mark.parametrize("past", [None, "x", 5, [1, "a", {"displayName": "이름만"}, {"userId": 3}], []])
async def test_M5_missing_or_malformed_past_members_are_ignored(past):
    body = room_json()
    if past is None:
        del body["pastMembers"]
    else:
        body["pastMembers"] = past
    r = await api_with(lambda req: httpx.Response(200, json=body)).room(ROOM)
    assert r.past_members == () and r.members == (Member(ME, "AI Agent"),)


async def test_M6_a_catch_up_message_from_someone_who_left_is_attributed_by_name(tmp_path):
    info = room(members=(Member(ME, "AI Agent"), Member(BOB, "tanminkwan")), past=(Member(GONE, "나간 사람 이름"),))
    info = RoomInfo(room_id=info.room_id, kind="direct", name="", members=info.members, past_members=info.past_members, last_seq=2)
    api = FakeChatApi([info], [chat_message(1, sender=GONE), chat_message(2, sender=BOB)])
    state, got = SqliteChatState(tmp_path / "chat.db"), []

    async def sink(req):
        got.append(req)

    d = ChatDispatcher(api=api, state=state, policy=ReplyPolicy(mention_name="AI Agent", write_allowed_users=()), sink=sink,
                       clock=lambda: datetime(2026, 10, 15, 7, 0, tzinfo=UTC), now_iso=lambda: NOW_ISO)
    state.register_room(ROOM, "direct", start_seq=0, now=NOW_ISO)
    await d.handle(HelloFrame(user_id=ME, room_ids=(ROOM,)))
    assert [r.requester for r in got] == ["나간 사람 이름", "tanminkwan"]

