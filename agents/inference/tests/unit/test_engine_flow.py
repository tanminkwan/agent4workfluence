"""요청 수행의 기본 흐름 (설계 부록 B.1, AC-I2). TDD red: engine이 아직 없다."""

from __future__ import annotations

import json

import pytest

from tests.fakes import (AGENT, CLOSE, NONCE, OPEN, TARGET, calls, cand, err, mcp, ok, rig, say, settings, tool_def)

pytestmark = pytest.mark.anyio

MEETING = {"items": [{"pageId": "0b6f2c1e-77aa-4f0e-9d51-3c2a1f4e8a10", "title": "10월 주간회의 회의록", "updatedAt": "2026-10-14T05:12:00Z"}]}


async def test_A1_search_then_answer():
    r = rig([calls(("search_pages", {"q": "회의록"})), say("'10월 주간회의 회의록' 페이지가 있습니다.")],
            candidates=[cand("search_pages", "r-a1"), cand("read_page", "r-b2")],
            handlers={mcp("search_pages"): ok(MEETING)})
    res = await r.run("회의록 페이지 찾아줘")

    assert res.complete is True and res.stop_reason is None
    assert res.answer == "'10월 주간회의 회의록' 페이지가 있습니다."
    # 모델에는 후보만, function 이름은 MCP 이름
    assert r.tool_names(0) == [mcp("search_pages"), mcp("read_page")]
    # 검색에서 얻은 revision이 expectedRevision으로 간다
    assert r.gateway.calls == [{"name": mcp("search_pages"), "args": {"q": "회의록"}, "revision": "r-a1"}]
    # 결과는 읽은 자료 표시로 감싸져 다음 차례에 들어간다
    tool_msg = [m for m in r.request(1).messages if m.role == "tool"][0]
    assert tool_msg.content.startswith(OPEN) and tool_msg.content.endswith(CLOSE)
    assert "10월 주간회의 회의록" in tool_msg.content
    assert tool_msg.tool_call_id == r.request(1).messages[-2].tool_calls[0].id
    # 기록
    [a] = res.actions
    assert (a.tool, a.revision, a.ok, a.error_code, a.target_request_id, a.write) == (mcp("search_pages"), "r-a1", True, None, "wf-req-1", False)
    assert res.usage.steps == 2 and res.usage.tool_calls == 1 and res.usage.write_calls == 0
    assert res.usage.prompt_tokens == 200 and res.usage.completion_tokens == 50


async def test_A2_chained_read_uses_previous_result():
    page_id = MEETING["items"][0]["pageId"]
    r = rig([calls(("search_pages", {"q": "온보딩"})), calls(("read_page", {"id": page_id})),
             say("온보딩 문서 요약: 입사 첫 주 일정과 계정 발급 절차를 안내합니다.")],
            candidates=[cand("search_pages"), cand("read_page")],
            handlers={mcp("search_pages"): ok(MEETING), mcp("read_page"): ok({"id": page_id, "title": "온보딩 안내", "body": "입사 첫 주 …"})})
    res = await r.run("온보딩 문서 찾아서 요약해줘")
    assert r.gateway.names() == [mcp("search_pages"), mcp("read_page")]
    last = r.request(2).messages
    assert [m.role for m in last].count("tool") == 2            # 앞 결과도 계속 들어 있다
    assert res.usage.steps == 3 and res.complete


async def test_A3_parallel_tool_calls_run_in_order():
    r = rig([calls(("list_notifications", {}), ("search_pages", {"q": "휴가"})), say("확인했습니다.")],
            candidates=[cand("list_notifications"), cand("search_pages")])
    res = await r.run("알림이랑 휴가 문서 같이 봐줘")
    assert r.gateway.names() == [mcp("list_notifications"), mcp("search_pages")]
    msgs = r.request(1).messages
    asst = [m for m in msgs if m.role == "assistant"][0]
    tools = [m for m in msgs if m.role == "tool"]
    assert [t.tool_call_id for t in tools] == [c.id for c in asst.tool_calls]
    assert res.usage.tool_calls == 2 and res.usage.steps == 2


async def test_A4_no_candidates_answers_without_tools():
    r = rig([say("제가 가진 tool로는 날씨를 알 수 없습니다.")], candidates=[])
    res = await r.run("오늘 서울 날씨 알려줘")
    assert r.request(0).tools == ()
    assert r.gateway.calls == []
    assert res.complete and res.actions == () and "날씨" in res.answer


async def test_A5_candidates_missing_from_mcp_are_dropped():
    only = [tool_def("search_pages"), tool_def("read_page")]
    r = rig([say("네")], candidates=[cand("search_pages"), cand("archive_page"), cand("read_page")], tools=only)
    await r.run("페이지 찾아줘")
    assert r.tool_names(0) == [mcp("search_pages"), mcp("read_page")]


async def test_A6_duplicate_candidates_keep_the_best_score():
    r = rig([say("네")], candidates=[cand("search_pages", "r-1", 0.91), cand("read_page", "r-1", 0.8), cand("search_pages", "r-1", 0.7)])
    await r.run("페이지 찾아줘")
    assert r.tool_names(0) == [mcp("search_pages"), mcp("read_page")]


async def test_A7_requester_never_reaches_the_model():
    r = rig([calls(("search_pages", {"q": "x"})), say("완료")], candidates=[cand("search_pages")])
    await r.run("페이지 찾아줘", requester="kim.minsu@example.com")
    for req in r.llm.requests:
        blob = json.dumps([m.content for m in req.messages], ensure_ascii=False)
        assert "kim.minsu" not in blob and "svc-" not in blob


async def test_A8_system_prompt_placeholders_are_filled():
    r = rig([say("네")], candidates=[])
    await r.run("안녕")
    system = r.request(0).messages[0]
    assert system.role == "system"
    assert f"너는 {AGENT}다" in system.content
    assert "2026-10-15T09:30:00+09:00" in system.content
    assert f"타겟: {TARGET}" in system.content
    assert f"{OPEN} 와 {CLOSE} 사이" in system.content
    assert "${" not in system.content


async def test_model_settings_are_passed_to_the_llm():
    cfg = settings(model="openai/gpt-oss-120b", max_output_tokens=777, temperature=0.1, reasoning_effort="low", timeout_seconds=42.0)
    r = rig([say("네")], candidates=[], cfg=cfg)
    await r.run("안녕")
    q = r.request(0)
    assert (q.model, q.max_output_tokens, q.temperature, q.reasoning_effort) == ("openai/gpt-oss-120b", 777, 0.1, "low")
    assert q.timeout_seconds <= 42.0


async def test_tool_error_is_returned_to_the_model_with_code_and_hint():
    r = rig([calls(("read_page", {"id": "nope"})), say("해당 페이지를 찾을 수 없습니다.")], candidates=[cand("read_page")],
            handlers={mcp("read_page"): err("TARGET_ERROR", "페이지가 없다", hint="search_pages로 id를 먼저 찾아라", targetCode="NOT_FOUND",
                                          httpStatus=404)})
    res = await r.run("그 페이지 열어줘")
    content = [m for m in r.request(1).messages if m.role == "tool"][0].content
    assert "TARGET_ERROR" in content and "NOT_FOUND" in content and "search_pages로 id를 먼저 찾아라" in content
    assert res.actions[0].ok is False and res.actions[0].error_code == "TARGET_ERROR" and res.complete


async def test_search_is_called_once_per_run_without_errors():
    r = rig([calls(("search_pages", {"q": "a"})), calls(("search_pages", {"q": "b"})), say("끝")], candidates=[cand("search_pages")])
    await r.run("a와 b를 찾아줘")
    assert len(r.search.queries) == 1 and r.gateway.refreshes == 0
    assert NONCE == "N0NCE"
