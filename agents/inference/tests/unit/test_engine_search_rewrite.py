"""tool 검색어를 모델이 다듬는다 (Phase 4 설계 20절, 2026-10-16 사용자 지시: "chatting 원문을 그대로 RAG search 하면 안 되고 text를 정제해서 search").

다듬기 프롬프트가 설정되어 있으면 검색 전에 모델을 한 번 불러, 앞선 대화와 지금 요청을 보고 검색 문장 한 줄을 받는다.
그 줄로 tool을 검색한다. 다듬기가 실패하거나 빈 답·잘린 답이면 원래 방식(앞선 요청 + 지금 요청)으로 검색한다.
설정이 없으면 Phase 4와 같다.
"""

from __future__ import annotations

import pytest

from a4w_clients.llm.base import LlmError
from tests.fakes import cand, rig, say, settings, turn

pytestmark = pytest.mark.anyio

TEMPLATE = "검색어를 한 줄로 써라.\n[대화]\n${history}\n[요청]\n${request}\n"
REWRITE = settings(search_rewrite_prompt=TEMPLATE, search_rewrite_max_output_tokens=256, search_rewrite_answer_max_chars=20)
HISTORY = [turn("You created a space. what the space name was?", "생성된 공간 이름은 'AI Agent 작업공간'입니다. 다른 것도 있습니다.")]


async def test_Q1_without_a_rewrite_prompt_the_raw_requests_are_searched_and_the_model_is_called_once():
    r = rig([say("네")], candidates=[], history=HISTORY)
    await r.run("No, other one.", conversation_id="c-1")
    assert r.search.queries == ["You created a space. what the space name was?\nNo, other one."]
    assert len(r.llm.requests) == 1


async def test_Q2_the_rewritten_line_is_what_gets_searched():
    r = rig([say("스페이스 목록을 보고 이름 확인"), say("Crazy Study입니다")], candidates=[cand("search_pages")], history=HISTORY,
            cfg=REWRITE)
    res = await r.run("No, other one.", conversation_id="c-1")
    assert r.search.queries == ["스페이스 목록을 보고 이름 확인"]
    assert res.answer == "Crazy Study입니다" and len(r.llm.requests) == 2


async def test_Q3_the_rewrite_call_sees_the_template_filled_with_history_and_request_and_no_tools():
    r = rig([say("스페이스 이름 확인"), say("네")], candidates=[], history=HISTORY, cfg=REWRITE)
    await r.run("No, other one.", conversation_id="c-1")
    first = r.request(0)
    assert first.tools == () and first.max_output_tokens == 256
    (msg,) = first.messages
    assert msg.role == "user"
    assert msg.content == ("검색어를 한 줄로 써라.\n[대화]\n"
                           "user: You created a space. what the space name was?\nassistant: " + HISTORY[0].answer[:20] + "\n"
                           "[요청]\nNo, other one.\n")


async def test_Q4_without_history_the_history_slot_is_empty_and_the_request_is_still_rewritten():
    r = rig([say("스페이스 목록 보기"), say("네")], candidates=[], cfg=REWRITE)
    await r.run("Show me the spaces")
    assert r.request(0).messages[0].content == "검색어를 한 줄로 써라.\n[대화]\n\n[요청]\nShow me the spaces\n"
    assert r.search.queries == ["스페이스 목록 보기"]


async def test_Q5_only_the_first_non_empty_line_is_used_and_it_is_capped():
    cfg = settings(search_rewrite_prompt=TEMPLATE, search_rewrite_max_output_tokens=256, search_rewrite_answer_max_chars=20,
                   search_query_max_chars=10)
    r = rig([say("\n  스페이스 목록을 보고 이름으로 찾기  \n설명 줄"), say("네")], candidates=[], cfg=cfg)
    await r.run("check it")
    assert r.search.queries == ["스페이스 목록을 보고 이름으로 찾기"[:10]]


@pytest.mark.parametrize("first", [LlmError("timeout", "x"), LlmError("unavailable", "x"), say("   "), say(None),
                                   say("스페이스 목", finish="length")])
async def test_Q6_a_failed_or_unusable_rewrite_falls_back_to_the_raw_query_and_the_turn_goes_on(first):
    r = rig([first, say("네")], candidates=[], history=HISTORY, cfg=REWRITE)
    res = await r.run("No, other one.", conversation_id="c-1")
    assert r.search.queries == ["You created a space. what the space name was?\nNo, other one."]
    assert res.complete and res.answer == "네"


async def test_Q7_rewrite_tokens_are_counted_but_not_as_a_step():
    r = rig([say("스페이스 목록", prompt_tokens=400, completion_tokens=30), say("네", prompt_tokens=1000)], candidates=[], cfg=REWRITE)
    res = await r.run("spaces?")
    assert res.usage.steps == 1
    assert res.usage.prompt_tokens == 1400 and res.usage.completion_tokens == 50
    assert res.usage.last_input_tokens == 1000          # 글자/토큰 보정은 본 호출로만 한다


async def test_Q8_history_beyond_the_search_turns_setting_is_not_shown_to_the_rewrite():
    cfg = settings(search_rewrite_prompt=TEMPLATE, search_rewrite_max_output_tokens=256, search_rewrite_answer_max_chars=20,
                   search_history_turns=1)
    hist = [turn("오래된 요청", "오래된 답"), turn("최근 요청", "최근 답")]
    r = rig([say("검색어"), say("네")], candidates=[], history=hist, cfg=cfg)
    await r.run("이어서", conversation_id="c-1")
    content = r.request(0).messages[0].content
    assert "최근 요청" in content and "오래된" not in content
