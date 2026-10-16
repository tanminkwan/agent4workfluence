"""engine 보강 — 요청별 쓰기 허용·채널 덧붙임 (Phase 5 설계 5.3·8절, 16절 1번, FR-C23·C32, NFR-C07).

TurnRequest의 선택 필드 두 개(allow_writes, channel_notes)는 기본값이 Phase 4 동작이다. 쓰기를 막을 때는 모델에 싣지 않는 것에
더해, 모델이 이름을 불러도 실행하지 않는다(모델이 최악으로 행동해도 코드가 막는다).
"""

from __future__ import annotations

import pytest

from a4w_inference.engine.models import TurnRequest
from tests.fakes import AGENT, calls, cand, mcp, ok, rig, say, tool_def

pytestmark = pytest.mark.anyio

NOTES = "채팅 창에는 평문으로 짧게 답하라. 이 요청자는 쓰기 권한이 없다."
MIXED = [cand("search_pages"), cand("read_page"), cand("create_page"), cand("add_comment")]


def system_of(r, i: int = 0) -> str:
    first = r.request(i).messages[0]
    assert first.role == "system"
    return first.content or ""


def test_defaults_are_the_phase4_behaviour():
    req = TurnRequest(agent_id=AGENT, text="회의록 찾아줘", requester="svc")
    assert req.allow_writes is True and req.channel_notes is None


# ---- 쓰기 허용 (FR-C32) ----

async def test_W1_writes_not_allowed_drops_write_tools_from_the_model_keeping_read_tools():
    r = rig([say("찾았습니다")], candidates=MIXED)
    await r.run("회의록 찾아줘", allow_writes=False)
    names = r.tool_names(0)
    assert mcp("search_pages") in names and mcp("read_page") in names
    assert mcp("create_page") not in names and mcp("add_comment") not in names


async def test_W2_writes_allowed_by_default_keeps_write_tools():
    r = rig([say("찾았습니다")], candidates=MIXED)
    await r.run("회의록 찾아줘")
    assert mcp("create_page") in r.tool_names(0) and mcp("add_comment") in r.tool_names(0)


async def test_W3_model_calling_a_blocked_write_tool_is_not_executed_and_gets_a_clear_error():
    r = rig([calls(("create_page", {"title": "공지", "body": "내용"})), say("권한이 없어 만들지 못했습니다")], candidates=MIXED,
            handlers={mcp("create_page"): ok({"id": "p-1"})})
    res = await r.run("공지 페이지 만들어줘", allow_writes=False)
    assert r.gateway.calls == []                                     # 실행하지 않았다
    tool_msg = [m for m in r.request(1).messages if m.role == "tool"][0].content
    assert "WRITE_NOT_ALLOWED" in tool_msg
    a = res.actions[0]
    assert a.ok is False and a.error_code == "WRITE_NOT_ALLOWED" and a.write is True
    assert res.usage.write_calls == 0 and res.complete is True and res.answer == "권한이 없어 만들지 못했습니다"


async def test_W4_an_unknown_name_is_still_not_a_candidate_when_writes_are_blocked():
    r = rig([calls(("delete_everything", {})), say("할 수 없습니다")], candidates=MIXED)
    res = await r.run("다 지워줘", allow_writes=False)
    assert r.gateway.calls == [] and res.actions[0].error_code == "NOT_A_CANDIDATE"


async def test_W5_read_tools_still_run_when_writes_are_blocked():
    r = rig([calls(("search_pages", {"q": "회의록"})), say("3건 찾았습니다")], candidates=MIXED,
            handlers={mcp("search_pages"): ok({"items": []})})
    res = await r.run("회의록 찾아줘", allow_writes=False)
    assert [c["name"] for c in r.gateway.calls] == [mcp("search_pages")] and res.actions[0].ok


async def test_W6_research_after_a_tool_set_change_keeps_write_tools_blocked():
    stale = [cand("search_pages", "r-old")]
    fresh = [cand("search_pages", "r-new"), cand("create_page", "r-new")]
    r = rig([calls(("search_pages", {"q": "회의록"})), calls(("create_page", {"title": "t", "body": "b"})), say("끝")],
            tools=[tool_def("search_pages", "r-new"), tool_def("create_page", "r-new")], search_results=[stale, fresh])
    res = await r.run("회의록 찾아서 정리 페이지 만들어줘", allow_writes=False)
    assert all(c["name"] != mcp("create_page") for c in r.gateway.calls)
    assert mcp("create_page") not in r.tool_names(1)
    assert res.actions[-1].error_code == "WRITE_NOT_ALLOWED"


# ---- 채널 덧붙임 (FR-C23) ----

async def test_N1_channel_notes_are_appended_to_the_rendered_system_prompt():
    plain = rig([say("네")], candidates=[cand("search_pages")])
    await plain.run("안녕")
    noted = rig([say("네")], candidates=[cand("search_pages")])
    await noted.run("안녕", channel_notes=NOTES)
    base = system_of(plain)
    assert system_of(noted).startswith(base) and system_of(noted).rstrip().endswith(NOTES)
    assert NOTES not in base


async def test_N2_without_notes_the_system_prompt_is_unchanged():
    a = rig([say("네")], candidates=[cand("search_pages")])
    await a.run("안녕")
    b = rig([say("네")], candidates=[cand("search_pages")])
    await b.run("안녕", channel_notes=None)
    c = rig([say("네")], candidates=[cand("search_pages")])
    await c.run("안녕", channel_notes="   ")
    assert system_of(a) == system_of(b) == system_of(c)


async def test_N3_notes_are_literal_text_not_a_template():
    notes = "가격은 $5, ${agent_id}는 그대로 둔다"
    r = rig([say("네")], candidates=[cand("search_pages")])
    await r.run("안녕", channel_notes=notes)
    assert system_of(r).rstrip().endswith(notes)


async def test_N4_notes_do_not_leak_into_the_tool_search_query_or_the_user_message():
    r = rig([say("네")], candidates=[cand("search_pages")])
    await r.run("회의록 찾아줘", channel_notes=NOTES)
    assert all(NOTES not in q for q in r.search.queries)
    assert all(NOTES not in c for c in r.contents(0, role="user"))
