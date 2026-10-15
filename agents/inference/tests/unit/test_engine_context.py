"""입력 크기(context) 관리 — 이력을 포함한 실제 입력 전체가 기준 (설계 6.7·6.7.1, 부록 B.6, AC-I4).

글자/토큰 비율을 1.0으로 두면 어림 토큰 = 글자 수라 크기를 눈으로 셀 수 있다. 정확한 직렬화 길이에 기대지 않고
"줄었는가/안 줄었는가"를 비교해 확인한다.
"""

from __future__ import annotations

import pytest

from a4w_clients.llm.base import LlmError
from tests.fakes import calls, cand, mcp, ok, rig, say, settings, turn

pytestmark = pytest.mark.anyio


def history(n: int, size: int = 300):
    return [turn(f"T{i}-" + "요" * size, f"A{i}-" + "답" * size) for i in range(1, n + 1)]


def sent_text(req) -> str:
    return "\n".join((m.content or "") for m in req.messages)


async def test_F1_fixed_parts_alone_exceed_the_limit():
    huge_tool = {"type": "object", "properties": {"q": {"type": "string", "description": "설" * 5000}}}
    from tests.fakes import tool_def
    r = rig([say("x")], candidates=[cand("search_pages")], tools=[tool_def("search_pages", schema=huge_tool)],
            cfg=settings(max_input_tokens=1000, safety_ratio=1.0, chars_per_token=1.0))
    res = await r.run("찾아줘")
    assert r.llm.requests == []
    assert res.complete is False and res.stop_reason == "CONTEXT_LIMIT" and res.answer is None


async def test_B5_F2_oldest_history_turns_are_dropped_first_and_the_latest_is_kept():
    r = rig([say("네")], candidates=[], history=history(6),
            cfg=settings(max_input_tokens=2000, safety_ratio=1.0, chars_per_token=1.0))
    res = await r.run("이번 요청: 제목은 주간회의", conversation_id="c")
    text = sent_text(r.request(0))
    assert "T6-" in text and "A6-" in text and "T1-" not in text and "A1-" not in text
    assert "이번 요청: 제목은 주간회의" in text                         # 이번 text는 절대 빠지지 않는다
    assert res.usage.history_dropped_turns >= 1
    assert sum(len(m.content or "") for m in r.request(0).messages) <= 2000
    assert res.complete


async def test_B5b_turns_are_dropped_whole_never_cut_in_half():
    r = rig([say("네")], candidates=[], history=history(4),
            cfg=settings(max_input_tokens=1500, safety_ratio=1.0, chars_per_token=1.0))
    await r.run("이번", conversation_id="c")
    text = sent_text(r.request(0))
    for i in range(1, 5):
        assert ("T%d-" % i in text) == ("A%d-" % i in text)           # 질문과 답이 함께 남거나 함께 빠진다


async def test_F3_measured_prompt_tokens_drive_the_decision_not_only_the_char_estimate():
    """첫 응답이 입력 크기를 글자 어림보다 훨씬 크게 보고하면, 다음 호출 전에 tool 결과를 줄인다."""
    body = {"body": "회" * 3000}
    cfg = settings(max_input_tokens=80_000, safety_ratio=1.0, chars_per_token=1.0, trim_keep_chars=100)
    r = rig([calls(("read_page", {"id": "p"}), prompt_tokens=79_000), say("요약했습니다")], candidates=[cand("read_page")],
            cfg=cfg, handlers={mcp("read_page"): ok(body)})
    res = await r.run("그 페이지 읽어줘")
    tool_msg = [m for m in r.request(1).messages if m.role == "tool"][0].content
    assert "줄임" in tool_msg and tool_msg.count("회") <= 100
    assert res.usage.trimmed_results == 1 and res.complete


async def test_F3b_same_flow_with_a_small_reported_size_trims_nothing():
    body = {"body": "회" * 3000}
    cfg = settings(max_input_tokens=80_000, safety_ratio=1.0, chars_per_token=1.0, trim_keep_chars=100)
    r = rig([calls(("read_page", {"id": "p"}), prompt_tokens=500), say("요약했습니다")], candidates=[cand("read_page")],
            cfg=cfg, handlers={mcp("read_page"): ok(body)})
    res = await r.run("그 페이지 읽어줘")
    tool_msg = [m for m in r.request(1).messages if m.role == "tool"][0].content
    assert tool_msg.count("회") == 3000 and "줄임" not in tool_msg and res.usage.trimmed_results == 0


async def test_F4_oldest_tool_results_are_trimmed_first_and_the_newest_stays():
    cfg = settings(max_input_tokens=60_000, safety_ratio=1.0, chars_per_token=1.0, trim_keep_chars=1000,
                   tool_result_max_chars=50_000)
    r = rig([calls(("read_page", {"id": "a"}), prompt_tokens=1_000),
             calls(("read_page", {"id": "b"}), prompt_tokens=31_500), say("두 페이지를 비교했습니다")],
            candidates=[cand("read_page")], cfg=cfg,
            handlers={mcp("read_page"): [ok({"body": "갑" * 30_000}), ok({"body": "을" * 30_000})]})
    res = await r.run("a와 b 페이지를 비교해줘")
    tools = [m.content for m in r.request(2).messages if m.role == "tool"]
    assert "줄임" in tools[0] and tools[0].count("갑") <= 1000
    assert "줄임" not in tools[1] and tools[1].count("을") == 30_000
    assert res.usage.trimmed_results == 1 and res.complete


async def test_F4b_when_trimming_cannot_help_the_run_ends_with_context_limit():
    cfg = settings(max_input_tokens=10_000, safety_ratio=1.0, chars_per_token=1.0, trim_keep_chars=500)
    r = rig([calls(("read_page", {"id": "a"}), prompt_tokens=9_900), say("x")], candidates=[cand("read_page")], cfg=cfg,
            handlers={mcp("read_page"): ok({"body": "갑" * 100})})
    res = await r.run("읽어줘")
    assert res.stop_reason == "CONTEXT_LIMIT" and res.complete is False and res.answer is None
    assert len(r.llm.requests) == 1 and len(res.actions) == 1         # 이미 한 일은 남긴다


async def test_F5_server_reports_context_exceeded_then_history_is_halved_once():
    r = rig([LlmError("context_length", "maximum context length is 131072"), say("네")], candidates=[], history=history(4, 100))
    res = await r.run("이번 요청", conversation_id="c")
    assert len(r.llm.requests) == 2
    second = sent_text(r.request(1))
    assert "T1-" not in second and "T2-" not in second and "T3-" in second and "T4-" in second
    assert res.usage.history_dropped_turns == 2 and res.complete


async def test_F5b_second_context_error_ends_with_context_limit_not_a_failure():
    r = rig([LlmError("context_length", "too long"), LlmError("context_length", "too long")], candidates=[], history=history(4, 100))
    res = await r.run("이번 요청", conversation_id="c")
    assert len(r.llm.requests) == 2
    assert res.complete is False and res.stop_reason == "CONTEXT_LIMIT" and res.answer is None


async def test_F6_safety_ratio_multiplies_the_limit():
    hist = history(4, 200)
    loose = rig([say("네")], candidates=[], history=hist, cfg=settings(max_input_tokens=2000, safety_ratio=1.0, chars_per_token=1.0))
    tight = rig([say("네")], candidates=[], history=hist, cfg=settings(max_input_tokens=2000, safety_ratio=0.5, chars_per_token=1.0))
    a = await loose.run("이번", conversation_id="c")
    b = await tight.run("이번", conversation_id="c")
    assert a.usage.history_dropped_turns == 0
    assert b.usage.history_dropped_turns >= 1


async def test_F7_measured_chars_per_token_from_the_last_run_is_used_for_the_first_estimate():
    """저장된 마지막 실측 비율이 토큰을 많이 세는 쪽(0.5자/토큰)이면, 설정의 낙관적 값(4자/토큰)보다 먼저 줄인다."""
    hist = history(4, 200)
    cfg = settings(max_input_tokens=2000, safety_ratio=1.0, chars_per_token=4.0)
    optimistic = rig([say("네")], candidates=[], history=hist, cfg=cfg)
    measured = rig([say("네")], candidates=[], history=hist, cfg=cfg, store_cpt=0.5)
    a = await optimistic.run("이번", conversation_id="c")
    b = await measured.run("이번", conversation_id="c")
    assert a.usage.history_dropped_turns == 0 and b.usage.history_dropped_turns >= 1


async def test_F7b_usage_exposes_the_last_actual_input_for_calibration():
    r = rig([calls(("list_pages", {}), prompt_tokens=1200), say("끝", prompt_tokens=2500)], candidates=[cand("list_pages")])
    res = await r.run("목록 보여줘")
    assert res.usage.last_input_tokens == 2500 and res.usage.last_input_chars > 0


async def test_F8_a_huge_current_text_is_never_cut_the_run_stops_instead():
    r = rig([say("x")], candidates=[], history=history(3, 50),
            cfg=settings(max_input_tokens=1000, safety_ratio=1.0, chars_per_token=1.0))
    res = await r.run("긴" * 5000, conversation_id="c")
    assert r.llm.requests == [] and res.stop_reason == "CONTEXT_LIMIT" and res.complete is False


async def test_trimmed_marker_says_how_much_was_cut():
    cfg = settings(max_input_tokens=5000, safety_ratio=1.0, chars_per_token=1.0, trim_keep_chars=200)
    r = rig([calls(("read_page", {"id": "a"}), prompt_tokens=4000), calls(("read_page", {"id": "b"}), prompt_tokens=4100), say("끝")],
            candidates=[cand("read_page")], cfg=cfg, handlers={mcp("read_page"): ok({"body": "가" * 3000})})
    await r.run("a, b 읽어줘")
    trimmed = [m.content for m in r.request(2).messages if m.role == "tool"][0]
    assert "줄임" in trimmed and "3" in trimmed          # 원래 길이(자)가 표시에 들어 있다
