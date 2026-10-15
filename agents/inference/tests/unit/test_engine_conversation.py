"""정보 부족 되묻기와 이어 가기 (설계 부록 B.2, FR-I10·I19, AC-I15). 사용자 지시의 핵심.

가짜 LLM은 "모델이 이렇게 답했다"를 대본으로 정한다. 모델이 실제로 되묻는지는 live 평가(L3·L5)가 본다.
여기서는 되물은 뒤 값을 더해 이어 갈 때 **코드가 하는 일**(이력 전달, 검색 질의, 크기 관리)을 고정한다.
"""

from __future__ import annotations

import pytest

from tests.fakes import AGENT, MARK, action, calls, cand, err, mcp, ok, rig, say, settings, turn

pytestmark = pytest.mark.anyio

CREATE_CANDS = [cand("create_page"), cand("search_pages")]


async def test_B1_missing_value_ends_with_a_question_and_no_tool_call():
    r = rig([say("페이지 제목은 무엇인가요?")], candidates=CREATE_CANDS)
    res = await r.run("페이지 하나 만들어줘", conversation_id="c-1")
    assert res.answer == "페이지 제목은 무엇인가요?"
    assert res.complete is True and res.stop_reason is None and res.actions == ()
    assert r.gateway.calls == []
    assert mcp("create_page") in r.tool_names(0)           # 도구는 실려 있었지만 모델이 부르지 않았다
    assert res.usage.steps == 1 and res.usage.tool_calls == 0


async def test_B2_follow_up_continues_the_conversation_like_memory():
    first = turn("페이지 하나 만들어줘", "페이지 제목은 무엇인가요?")
    follow = "제목은 '10월 팀 회식 공지', 내용은 '10/24 18:30 강남역 2번 출구 앞'"
    r = rig([calls(("create_page", {"title": "10월 팀 회식 공지", "body": "10/24 18:30 강남역 2번 출구 앞"})),
             say("'10월 팀 회식 공지' 페이지를 만들었습니다.")],
            candidates=CREATE_CANDS, history=[first],
            handlers={mcp("create_page"): ok({"id": "p-77", "title": "10월 팀 회식 공지"})})
    res = await r.run(follow, conversation_id="c-1")

    # 검색 질의에 앞선 요청자 text가 합쳐진다: 값만 더한 짧은 text로는 create_page를 찾을 수 없다
    assert r.search.queries == ["페이지 하나 만들어줘\n" + follow]
    # 모델 메시지: system, 앞선 요청자 text, 앞선 질문(assistant), 이번 text
    roles = [(m.role, m.content) for m in r.request(0).messages][1:]
    assert roles == [("user", "페이지 하나 만들어줘"), ("assistant", "페이지 제목은 무엇인가요?"), ("user", follow)]
    # 이력을 읽을 때 agent·conversation·한도를 넘긴다
    assert r.store.history_calls == [(AGENT, "c-1", 50)]
    # 이어서 실제로 실행되고 표시가 붙는다
    assert r.gateway.calls[0]["args"] == {"title": "10월 팀 회식 공지", "body": "10/24 18:30 강남역 2번 출구 앞" + MARK}
    assert res.complete and res.usage.write_calls == 1


async def test_B3_history_has_tool_names_but_no_result_bodies():
    """이력 설계의 현실적 한계: 앞 차례의 알림 id는 이력에 없다 → 모델이 다시 조회해야 한다."""
    first = turn("내 알림 보여줘", "안 읽은 알림이 2개 있습니다: 댓글 알림, 멘션 알림.", action("list_notifications"))
    r = rig([calls(("list_notifications", {})), calls(("mark_notification_read", {"id": "n-4821"})),
             say("첫 번째 알림을 읽음 처리했습니다.")],
            candidates=[cand("list_notifications"), cand("mark_notification_read")], history=[first],
            handlers={mcp("list_notifications"): ok({"items": [{"id": "n-4821", "type": "COMMENT"}, {"id": "n-4822", "type": "MENTION"}]}),
                      mcp("mark_notification_read"): ok({"id": "n-4821", "read": True})})
    res = await r.run("첫 번째 거 읽음 처리해줘", conversation_id="c-2")
    msgs = r.request(0).messages
    assistant = [m for m in msgs if m.role == "assistant"][0]
    assert assistant.content == ("안 읽은 알림이 2개 있습니다: 댓글 알림, 멘션 알림.\n"
                                 "[실행: workfluence__list_notifications ok]")
    assert "n-4821" not in " ".join(m.content or "" for m in msgs)
    assert r.gateway.names() == [mcp("list_notifications"), mcp("mark_notification_read")]
    assert res.complete and res.usage.write_calls == 1


async def test_B3b_failed_action_in_history_shows_error_code():
    first = turn("그 글 고쳐줘", "수정하지 못했습니다.", action("update_page", ok_=False, code="TARGET_ERROR", write=True))
    r = rig([say("다시 시도할게요")], candidates=[], history=[first])
    await r.run("다시 해줘", conversation_id="c-3")
    asst = [m for m in r.request(0).messages if m.role == "assistant"][0].content
    assert asst.endswith("[실행: workfluence__update_page TARGET_ERROR]")


async def test_B4_invented_values_come_back_as_input_invalid_and_the_model_can_ask():
    bad = err("INPUT_INVALID", "입력이 스키마와 맞지 않는다", hint="title은 1자 이상이어야 한다",
              details={"fields": [{"path": "title", "problem": "minLength"}]}, request_id=None)
    r = rig([calls(("create_page", {"title": "", "body": ""})), say("페이지 제목과 내용을 알려 주세요.")],
            candidates=CREATE_CANDS, handlers={mcp("create_page"): bad})
    res = await r.run("페이지 만들어줘")
    content = [m for m in r.request(1).messages if m.role == "tool"][0].content
    assert "INPUT_INVALID" in content and "title은 1자 이상이어야 한다" in content and '"title"' in content
    assert res.complete and res.answer == "페이지 제목과 내용을 알려 주세요."
    assert res.actions[0].ok is False and res.actions[0].error_code == "INPUT_INVALID" and res.actions[0].write is True


async def test_B8_without_conversation_id_there_is_no_history_and_the_query_is_the_text():
    r = rig([say("네")], candidates=CREATE_CANDS, history=[turn("이전 요청", "이전 답")])
    await r.run("회의록 찾아줘")
    assert r.store.history_calls == []
    assert r.search.queries == ["회의록 찾아줘"]
    assert len(r.request(0).messages) == 2


async def test_search_query_uses_only_the_latest_n_previous_requester_texts_oldest_first():
    hist = [turn(f"요청{i}", f"답{i}") for i in range(1, 6)]
    r = rig([say("네")], candidates=[], history=hist, cfg=settings(search_history_turns=2))
    await r.run("이번 요청", conversation_id="c")
    assert r.search.queries == ["요청4\n요청5\n이번 요청"]          # 앞선 '답'은 질의에 넣지 않는다


async def test_B9_search_query_is_capped_and_cut_from_the_old_side():
    hist = [turn("가" * 400, "답"), turn("나" * 400, "답")]
    r = rig([say("네")], candidates=[], history=hist, cfg=settings(search_query_max_chars=120))
    await r.run("이번에 값을 알려줄게: 제목은 주간회의", conversation_id="c")
    q = r.search.queries[0]
    assert len(q) <= 120 and q.endswith("이번에 값을 알려줄게: 제목은 주간회의")
    assert "가" not in q


async def test_history_limit_comes_from_settings():
    r = rig([say("네")], candidates=[], cfg=settings(history_max_turns=7))
    await r.run("안녕", conversation_id="c")
    assert r.store.history_calls == [(AGENT, "c", 7)]
