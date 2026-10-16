"""요청 글에서 호명을 뺀다 (Phase 5 설계 20절, 2026-10-16 사용자 시험).

여럿 방은 메시지마다 에이전트 이름을 불러야 한다. 이름이 요청 글에 남으면 이력과 함께 tool 검색어에 되풀이되어
검색을 흐린다(실측: seq 10 검색어에서 이름만 빼면 list_spaces 9위 → 4위). 그래서 수행 로직에 넘기기 전에 뺀다.
이름만 있는 메시지는 빼면 빈 글이 되므로 원문을 그대로 둔다.
"""

from __future__ import annotations

import pytest

from a4w_inference.channels.chat.policy import strip_mention
from tests.unit.test_chat_worker import incoming, make

pytestmark = pytest.mark.anyio
NAME = "AI Agent"


@pytest.mark.parametrize("body, expected", [
    ("AI Agent, You created a space. what the space name was?", "You created a space. what the space name was?"),
    ("AI Agent. The name was 'Crazy Study',  check it.", "The name was 'Crazy Study',  check it."),
    ("Mr. AI agent! make a space", "Mr. make a space"),
    ("ai   AGENT 스페이스 목록 알려줘", "스페이스 목록 알려줘"),
    ("방금 만든 거 확인해줘 AI Agent", "방금 만든 거 확인해줘"),
    ("이건 AI Agent 가 할 일이야", "이건 가 할 일이야"),
    ("스페이스 목록 알려줘", "스페이스 목록 알려줘"),
])
def test_M1_the_mention_and_the_punctuation_right_after_it_are_removed(body, expected):
    assert strip_mention(body, NAME) == expected


@pytest.mark.parametrize("body", ["AI Agent!", "  AI agent  ", "AI Agent, AI Agent."])
def test_M2_a_message_that_is_only_the_name_keeps_its_original_text(body):
    assert strip_mention(body, NAME) == body.strip()


@pytest.mark.parametrize("body, expected", [
    ("@AI Agent 스페이스 목록 알려줘", "스페이스 목록 알려줘"),
    ("@tanminkwan 을 스페이스에 넣어줘 @AI Agent", "@tanminkwan 을 스페이스에 넣어줘"),
    ("@ai agent, 목록", "목록"),
])
def test_M6_an_at_sign_right_before_the_name_goes_with_it(body, expected):
    """채팅 멘션(@이름, 2026-10-16 workf 기능 추가 예정). 다른 사람의 @멘션은 그대로 둔다."""
    assert strip_mention(body, NAME) == expected


def test_M3_names_with_regex_characters_are_matched_literally():
    assert strip_mention("bot.v2 (beta) 목록 보여줘", "bot.v2 (beta)") == "목록 보여줘"
    assert strip_mention("botXv2 목록", "bot.v2") == "botXv2 목록"


async def test_M4_the_worker_submits_the_text_without_the_mention(tmp_path):
    env = make(tmp_path, mention_name=NAME)
    await env.run(incoming(5, body="AI Agent. 스페이스 목록 알려줘", kind="group"))
    assert env.submitter.submits[0].text == "스페이스 목록 알려줘"


async def test_M5_without_a_mention_name_the_text_is_unchanged(tmp_path):
    env = make(tmp_path)
    await env.run(incoming(5, body="AI Agent. 스페이스 목록 알려줘"))
    assert env.submitter.submits[0].text == "AI Agent. 스페이스 목록 알려줘"


async def test_M7_the_worker_also_strips_my_display_name_in_the_room(tmp_path):
    from a4w_inference.channels.chat.models import IncomingRequest
    from tests.chat_fakes import ME, ALICE, chat_message, members, room_info
    env = make(tmp_path, mention_name=NAME)
    room = room_info(kind="group", member_list=members((ME, "AI Bot"), (ALICE, "tiffanie")))
    await env.run(IncomingRequest(room=room, message=chat_message(5, "@AI Bot 스페이스 목록 알려줘"), requester="tiffanie",
                                  allow_writes=False, my_user_id=ME))
    assert env.submitter.submits[0].text == "스페이스 목록 알려줘"
