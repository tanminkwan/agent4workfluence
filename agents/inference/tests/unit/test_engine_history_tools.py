"""앞선 차례에서 성공한 tool은 후보에 넣는다 (Phase 4 설계 20.3절, 2026-10-16 채팅 실호출).

"아니 다른 거", "그거 확인해 줘" 같은 이어 가는 요청은 대개 앞 차례와 같은 것을 다룬다. 다듬은 검색어가 흔들려
그 tool이 검색 상위에서 빠져도(실측: list_spaces → NOT_A_CANDIDATE) 모델이 쓸 수 있게 한다.
검색 창(INFERENCE_SEARCH_HISTORY_TURNS) 안의 차례만 보고, 실패한 실행·지금 서빙하지 않는 tool은 넣지 않는다.
risk·쓰기 허용 규칙은 검색 후보와 똑같이 적용한다.
"""

from __future__ import annotations

import pytest

from tests.fakes import action, calls, cand, mcp, ok, rig, say, settings, tool_def, turn, ALL_TOOLS

pytestmark = pytest.mark.anyio
ON = settings(search_keep_history_tools=True)


def asked_about_spaces(*actions):
    return [turn("스페이스 목록 알려줘", "A, B, Crazy Study.", *actions)]


async def test_K1_a_tool_that_succeeded_in_a_recent_turn_is_offered_even_if_search_missed_it():
    r = rig([calls(("list_pages", {})), say("Crazy Study 있습니다")], candidates=[cand("search_pages")],
            history=asked_about_spaces(action("list_pages")), cfg=ON)
    res = await r.run("그 중에 Crazy Study 확인해줘", conversation_id="c-1")
    assert set(r.tool_names(0)) == {mcp("search_pages"), mcp("list_pages")}
    assert res.actions[0].ok and r.gateway.calls[0]["revision"] == "r-1"     # 지금 서빙하는 revision으로 부른다


async def test_K2_search_candidates_come_first_and_a_tool_is_not_offered_twice():
    r = rig([say("네")], candidates=[cand("search_pages"), cand("list_pages")], history=asked_about_spaces(action("list_pages")), cfg=ON)
    await r.run("다시", conversation_id="c-1")
    assert r.tool_names(0) == [mcp("search_pages"), mcp("list_pages")]


async def test_K3_failed_actions_and_tools_no_longer_served_are_not_added():
    tools = [tool_def(n) for n in ALL_TOOLS if n != "read_page"]
    r = rig([say("네")], candidates=[], tools=tools,
            history=asked_about_spaces(action("list_pages", ok_=False, code="TARGET_ERROR"), action("read_page")), cfg=ON)
    await r.run("다시", conversation_id="c-1")
    assert r.tool_names(0) == []


async def test_K4_only_turns_inside_the_search_history_window_count():
    cfg = settings(search_keep_history_tools=True, search_history_turns=1)
    hist = [turn("오래된", "답", action("list_notifications")), turn("최근", "답", action("list_pages"))]
    r = rig([say("네")], candidates=[], history=hist, cfg=cfg)
    await r.run("이어서", conversation_id="c-1")
    assert r.tool_names(0) == [mcp("list_pages")]


async def test_K5_a_write_tool_from_history_follows_the_write_permission():
    hist = asked_about_spaces(action("add_comment", write=True))
    denied = rig([say("못 합니다")], candidates=[], history=hist, cfg=ON)
    await denied.run("한 번 더 달아줘", conversation_id="c-1", allow_writes=False)
    assert denied.tool_names(0) == []
    allowed = rig([say("네")], candidates=[], history=hist, cfg=ON)
    await allowed.run("한 번 더 달아줘", conversation_id="c-1")
    assert allowed.tool_names(0) == [mcp("add_comment")]


async def test_K6_off_by_setting_keeps_phase4_behavior():
    r = rig([say("네")], candidates=[], history=asked_about_spaces(action("list_pages")))
    await r.run("다시", conversation_id="c-1")
    assert r.tool_names(0) == []


async def test_K7_without_a_conversation_nothing_is_added():
    r = rig([say("네")], candidates=[cand("search_pages")], handlers={mcp("search_pages"): ok([])}, cfg=ON)
    await r.run("목록")
    assert r.tool_names(0) == [mcp("search_pages")]
