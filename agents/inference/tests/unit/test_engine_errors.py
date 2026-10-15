"""tool 오류·버전 불일치·목록 바뀜(부록 B.4), 한도와 LLM·MCP 장애(부록 B.5). AC-I3·I4."""

from __future__ import annotations

import pytest

from a4w_clients.llm.base import LlmError
from a4w_inference.engine.models import McpUnavailable, RunFailed, SearchUnavailable
from tests.fakes import (MARK, ScriptedLlm, calls, cand, err, mcp, ok, rig, say, settings, tool_def)

pytestmark = pytest.mark.anyio


# ---------------- B.4 tool 오류 · 다시 찾기 ----------------

async def test_D1_edit_conflict_is_fed_back_with_hint_and_the_model_recovers():
    conflict = err("TARGET_ERROR", "다른 사람이 먼저 수정했다", targetCode="VERSION_CONFLICT", httpStatus=409,
                   hint="페이지를 다시 읽어 currentVersionNo를 baseVersionNo로 넣고 다시 호출하라")
    r = rig([calls(("update_page", {"id": "p-1", "baseVersionNo": 3, "title": "t", "body": "수정본"})),
             calls(("read_page", {"id": "p-1"})),
             calls(("update_page", {"id": "p-1", "baseVersionNo": 4, "title": "t", "body": "수정본"})),
             say("충돌을 해결해 수정했습니다.")],
            candidates=[cand("update_page"), cand("read_page")],
            handlers={mcp("update_page"): [conflict, ok({"id": "p-1", "versionNo": 5})],
                      mcp("read_page"): ok({"id": "p-1", "currentVersionNo": 4, "body": "…"})})
    res = await r.run("p-1 본문을 '수정본'으로 바꿔줘")
    first_tool_msg = [m for m in r.request(1).messages if m.role == "tool"][0].content
    assert "VERSION_CONFLICT" in first_tool_msg and "baseVersionNo" in first_tool_msg
    assert res.complete and res.answer == "충돌을 해결해 수정했습니다."
    assert [a.ok for a in res.actions] == [False, True, True]
    assert res.usage.write_calls == 2 and res.usage.tool_calls == 3


async def test_D2_rate_limited_error_reaches_the_model_unchanged():
    limited = err("RATE_LIMITED", "호출 한도", hint="잠시 뒤 다시 시도하라", retryable=True)
    r = rig([calls(("search_pages", {"q": "a"})), say("지금은 조회할 수 없어 잠시 뒤 다시 시도해 주세요.")],
            candidates=[cand("search_pages")], handlers={mcp("search_pages"): limited})
    res = await r.run("a 찾아줘")
    content = [m for m in r.request(1).messages if m.role == "tool"][0].content
    assert "RATE_LIMITED" in content and '"retryable": true' in content
    assert res.complete


async def test_D3_same_error_repeated_is_cut_off():
    denied = err("TARGET_ERROR", "권한이 없다", targetCode="FORBIDDEN", httpStatus=403, hint="이 계정은 쓸 수 없다")
    same = ("read_page", {"id": "secret-page"})
    r = rig([calls(same), calls(same), calls(same), say("읽을 수 없습니다")], candidates=[cand("read_page")],
            cfg=settings(max_same_error=2), handlers={mcp("read_page"): denied})
    res = await r.run("그 비공개 페이지 읽어줘")
    assert res.complete is False and res.stop_reason == "LIMIT_REPEATED_ERROR" and res.answer is None
    assert len(r.gateway.calls) == 2 and len(res.actions) == 2


async def test_D4_revision_mismatch_refreshes_and_retries_the_same_call_invisibly():
    r = rig([calls(("search_pages", {"q": "휴가 규정"})), say("휴가 규정 문서를 찾았습니다.")],
            tools=[tool_def("search_pages", "r-new")], search_results=[[cand("search_pages", "r-old")], [cand("search_pages", "r-new")]],
            handlers={mcp("search_pages"): ok({"items": []})})
    res = await r.run("휴가 규정 찾아줘")
    assert len(r.search.queries) == 2 and r.gateway.refreshes >= 1
    assert [c["revision"] for c in r.gateway.calls] == ["r-old", "r-new"]
    msgs = [m for m in r.request(1).messages if m.role == "tool"]
    assert len(msgs) == 1 and "REVISION_MISMATCH" not in msgs[0].content        # 모델에는 오류가 보이지 않는다
    assert res.complete and len(res.actions) == 1 and res.actions[0].ok and res.usage.tool_calls == 1


async def test_D5_revision_mismatch_that_persists_is_reported_and_does_not_loop():
    r = rig([calls(("search_pages", {"q": "x"})), say("tool 정보가 아직 맞지 않아 처리하지 못했습니다.")],
            tools=[tool_def("search_pages", "r-new")], search_results=[[cand("search_pages", "r-old")]])
    res = await r.run("x 찾아줘")
    assert len(r.search.queries) == 2                         # 처음 + 다시 찾기 1번, 그 이상 없음
    content = [m for m in r.request(1).messages if m.role == "tool"][0].content
    assert "REVISION_MISMATCH" in content
    assert res.complete and res.actions[0].error_code == "REVISION_MISMATCH"


async def test_D6_tool_vanished_triggers_research_and_the_model_gets_the_new_candidates():
    tools = [tool_def("search_pages"), tool_def("read_page_by_title")]
    r = rig([calls(("read_page", {"id": "p-1"})), say("제목으로 읽겠습니다.")],
            tools=tools, search_results=[[cand("read_page"), cand("search_pages")], [cand("read_page_by_title"), cand("search_pages")]])
    # 첫 정의에는 read_page가 있었는데 곧 사라진 것으로 만든다
    r.gateway.tools[mcp("read_page")] = tool_def("read_page")
    original_call = r.gateway.call

    async def vanish_then_call(*a, **k):
        r.gateway.remove(mcp("read_page"))
        return await original_call(*a, **k)
    r.gateway.call = vanish_then_call
    res = await r.run("p-1 읽어줘")
    content = [m for m in r.request(1).messages if m.role == "tool"][0].content
    assert "TOOL_SET_REFRESHED" in content
    assert mcp("read_page_by_title") in r.tool_names(1) and mcp("read_page") not in r.tool_names(1)
    assert len(r.search.queries) == 2 and res.complete


async def test_D7_list_changed_during_a_turn_triggers_one_research_before_the_call():
    def bump(request):                                        # 모델이 생각하는 사이 MCP 목록이 바뀐다
        r.gateway.replace(tool_def("search_pages", "r-2"))
        return calls(("search_pages", {"q": "a"}))
    r = rig([bump, calls(("search_pages", {"q": "b"})), say("끝")], tools=[tool_def("search_pages", "r-1")],
            search_results=[[cand("search_pages", "r-1")], [cand("search_pages", "r-2")]])
    res = await r.run("a와 b를 찾아줘")
    assert len(r.search.queries) == 2                          # 목록 바뀜 한 번당 다시 찾기 한 번
    assert r.gateway.calls[0]["revision"] == "r-2"
    assert res.complete and all(a.ok for a in res.actions)


async def test_D7b_research_budget_is_per_request():
    def bump(request):
        r.gateway.replace(tool_def("search_pages", f"r-{r.gateway.gen + 1}"))
        return calls(("search_pages", {"q": "a"}))
    r = rig([bump, bump, say("끝")], tools=[tool_def("search_pages", "r-1")], cfg=settings(retry_search_max=1),
            search_results=[[cand("search_pages", "r-1")]])
    await r.run("찾아줘")
    assert len(r.search.queries) <= 2


async def test_D8_search_failure_during_research_fails_the_run_but_keeps_writes():
    r = rig([calls(("create_page", {"title": "공지", "body": "내용"})), calls(("search_pages", {"q": "x"})), say("x")],
            tools=[tool_def("create_page", "r-1"), tool_def("search_pages", "r-2")],
            search_results=[[cand("create_page", "r-1"), cand("search_pages", "r-old")], SearchUnavailable("AI Pro Plus 응답 없음")],
            handlers={mcp("create_page"): ok({"id": "p-1"})})
    with pytest.raises(RunFailed) as e:
        await r.run("공지를 만들고 x도 찾아줘")
    assert e.value.code == "SEARCH_UNAVAILABLE"
    assert [a.tool for a in e.value.actions][0] == mcp("create_page") and e.value.actions[0].ok


async def test_D9_huge_tool_result_is_cut_and_marked():
    huge = {"body": "가" * 5000}
    r = rig([calls(("read_page", {"id": "p"})), say("길어서 일부만 읽었습니다")], candidates=[cand("read_page")],
            cfg=settings(tool_result_max_chars=1000), handlers={mcp("read_page"): ok(huge)})
    await r.run("읽어줘")
    content = [m for m in r.request(1).messages if m.role == "tool"][0].content
    assert content.count("가") <= 1000 and "줄임" in content


async def test_D10_long_error_message_is_capped_too():
    r = rig([calls(("read_page", {"id": "p"})), say("실패")], candidates=[cand("read_page")], cfg=settings(tool_result_max_chars=500),
            handlers={mcp("read_page"): err("TARGET_ERROR", "나" * 4000)})
    await r.run("읽어줘")
    assert len([m for m in r.request(1).messages if m.role == "tool"][0].content) < 1200


# ---------------- B.5 한도 ----------------

async def test_E1_endless_tool_calls_hit_the_step_limit_but_keep_actions():
    r = rig([calls(("list_pages", {})) for _ in range(20)], candidates=[cand("list_pages")], cfg=settings(max_steps=4))
    res = await r.run("페이지를 전부 나열해줘")
    assert res.complete is False and res.stop_reason == "LIMIT_STEPS" and res.answer is None
    assert len(r.llm.requests) == 4 and len(res.actions) == 4


async def test_E2_tool_call_limit_runs_only_what_is_left_of_a_batch():
    five = calls(*[("search_pages", {"q": str(i)}) for i in range(5)])
    r = rig([five], candidates=[cand("search_pages")], cfg=settings(max_tool_calls=2))
    res = await r.run("다섯 가지를 한꺼번에 찾아줘")
    assert len(r.gateway.calls) == 2 and res.stop_reason == "LIMIT_TOOL_CALLS" and res.complete is False and len(res.actions) == 2


async def test_E3_time_limit_uses_the_clock_and_caps_the_llm_timeout():
    def slow(request):
        r.clock.advance(200)
        return calls(("list_pages", {}))
    r = rig([slow, slow, say("x")], candidates=[cand("list_pages")], cfg=settings(max_seconds=300.0, timeout_seconds=60.0))
    res = await r.run("느린 작업")
    assert res.stop_reason == "LIMIT_TIME" and res.complete is False and res.answer is None


async def test_E3b_llm_timeout_never_exceeds_the_remaining_time():
    def slow(request):
        r.clock.advance(70)
        return calls(("list_pages", {}))
    r = rig([slow, say("끝")], candidates=[cand("list_pages")], cfg=settings(max_seconds=100.0, timeout_seconds=60.0))
    await r.run("작업")
    assert r.request(0).timeout_seconds == 60.0
    assert r.request(1).timeout_seconds <= 30.0
    assert all(t <= 30.0 for t in r.gateway.timeouts)


async def test_E4_output_cut_by_token_limit():
    r = rig([say("앞부분만 나오고 잘림", finish="length")], candidates=[])
    res = await r.run("아주 긴 글을 써줘")
    assert res.complete is False and res.stop_reason == "LLM_LENGTH" and res.answer is None
    assert len(r.llm.requests) == 1


async def test_E5_auth_failure_fails_fast():
    r = rig([LlmError("auth", "LLM 인증 실패 (401)")], candidates=[])
    with pytest.raises(RunFailed) as e:
        await r.run("안녕")
    assert e.value.code == "LLM_AUTH" and len(r.llm.requests) == 1


@pytest.mark.parametrize("kind,code", [("timeout", "LLM_TIMEOUT"), ("rate_limit", "LLM_RATE_LIMIT"),
                                       ("unavailable", "LLM_UNAVAILABLE"), ("output_format", "LLM_BAD_RESPONSE")])
async def test_E6_llm_errors_map_to_failure_codes_and_keep_prior_writes(kind, code):
    r = rig([calls(("create_page", {"title": "공지", "body": "내용"})), LlmError(kind, "x")], candidates=[cand("create_page")],
            handlers={mcp("create_page"): ok({"id": "p-1"})})
    with pytest.raises(RunFailed) as e:
        await r.run("공지 만들어줘")
    assert e.value.code == code and len(e.value.actions) == 1 and e.value.actions[0].write is True
    assert e.value.usage.write_calls == 1


async def test_E7_mcp_down_mid_run_fails_with_actions_kept():
    r = rig([calls(("create_page", {"title": "a", "body": "b"})), calls(("search_pages", {"q": "x"}))],
            candidates=[cand("create_page"), cand("search_pages")],
            handlers={mcp("create_page"): ok({"id": "p-1"}), mcp("search_pages"): McpUnavailable("자식 프로세스가 죽었다")})
    with pytest.raises(RunFailed) as e:
        await r.run("만들고 찾아줘")
    assert e.value.code == "MCP_UNAVAILABLE" and len(e.value.actions) == 1


async def test_E8_unexpected_exception_is_internal_and_leaks_nothing():
    secret = "token-eyJabc-SECRET"
    r = rig([calls(("search_pages", {"q": "회의록 비밀 요청"}))], candidates=[cand("search_pages")],
            handlers={mcp("search_pages"): KeyError(secret)})
    with pytest.raises(RunFailed) as e:
        await r.run("회의록 비밀 요청")
    text = f"{e.value} {e.value.code} {e.value.message}"
    assert e.value.code == "INTERNAL" and secret not in text and "회의록 비밀 요청" not in text


async def test_E9_search_failure_before_anything_else():
    r = rig([say("x")], search_results=[SearchUnavailable("down")])
    with pytest.raises(RunFailed) as e:
        await r.run("찾아줘")
    assert e.value.code == "SEARCH_UNAVAILABLE" and r.llm.requests == [] and e.value.actions == ()


@pytest.mark.parametrize("empty", [None, "", "   "])
async def test_E10_empty_model_output_is_not_treated_as_an_answer(empty):
    r = rig([say(empty)], candidates=[])
    res = await r.run("안녕")
    assert res.complete is False and res.stop_reason == "EMPTY_ANSWER" and res.answer is None


async def test_scripted_llm_guard():
    llm = ScriptedLlm()
    with pytest.raises(AssertionError):
        llm.chat(None)
    assert MARK
