"""대화 기록에 id를 남긴다 (Phase 4 설계 19절, 2026-10-16 사용자 지시: "api 처리 후 받은 생성 결과(주로 id)는 기억하고 있게 해").

성공한 tool 결과가 객체면 맨 윗단의 식별자 모양 값(refs)을 기록하고, 다음 차례의 이력 줄에 인자의 식별자 값과 함께 보인다.
공백이 있는 글(이름·제목·본문)은 담지 않는다(결과는 타겟 자료라 지시를 끼워 넣을 수 있다).
"""

from __future__ import annotations

import pytest

from a4w_inference.engine.models import ActionSummary, HistoryTurn
from a4w_inference.storage.sqlite_db import SqliteInferenceDb
from tests.fakes import cand, calls, err, mcp, ok, result, rig, say, settings

pytestmark = pytest.mark.anyio

SPACE = "f8e35749-84cc-4e10-a297-720e9d1a21e7"


def act(name, *, args=None, refs=None, ok_=True, code=None, write=True):
    return ActionSummary(tool=mcp(name), revision="r-1", ok=ok_, error_code=code, target_request_id="wf-1", write=write,
                         args=dict(args or {}), refs=dict(refs or {}))


# ---- 기록: 결과의 식별자 값 ----

async def test_R1_identifier_values_of_an_object_result_are_remembered_but_free_text_is_not():
    created = {"id": "p-77", "title": "10월 회식 공지", "currentVersionNo": 1, "slug": "oct-dinner", "draft": False,
               "owner": {"id": "u-1"}}
    r = rig([calls(("create_page", {"title": "10월 회식 공지", "body": "강남역"})), say("만들었습니다")],
            candidates=[cand("create_page")], handlers={mcp("create_page"): ok(created)})
    res = await r.run("회식 공지 페이지 만들어줘")
    assert res.actions[0].refs == {"id": "p-77", "currentVersionNo": "1", "slug": "oct-dinner"}


async def test_R2_list_results_and_failures_leave_no_refs():
    r = rig([calls(("search_pages", {"q": "회의록"})), calls(("read_page", {"id": "p-1"})), say("끝")],
            candidates=[cand("search_pages"), cand("read_page")],
            handlers={mcp("search_pages"): ok([{"id": "p-1"}, {"id": "p-2"}]), mcp("read_page"): err("NOT_FOUND")})
    res = await r.run("회의록 찾아 읽어줘")
    assert [a.refs for a in res.actions] == [{}, {}]


async def test_R3_refs_are_capped_by_the_setting():
    many = {f"k{i}": f"v{i}" for i in range(10)}
    r = rig([calls(("create_page", {"title": "t", "body": "b"})), say("네")], candidates=[cand("create_page")],
            handlers={mcp("create_page"): ok(many)}, cfg=settings(history_refs_max=3))
    res = await r.run("만들어줘")
    assert res.actions[0].refs == {"k0": "v0", "k1": "v1", "k2": "v2"}


@pytest.mark.parametrize("value", ["has space", "x" * 65, "a]b", "줄\n바꿈", "", "한글값"])
async def test_R4_values_that_are_not_identifier_shaped_are_dropped(value):
    r = rig([calls(("create_page", {"title": "t", "body": "b"})), say("네")], candidates=[cand("create_page")],
            handlers={mcp("create_page"): ok({"id": "p-1", "other": value})})
    res = await r.run("만들어줘")
    assert res.actions[0].refs == {"id": "p-1"}


# ---- 이력 줄: 다음 차례가 id를 안다 ----

async def test_H1_the_next_turn_sees_created_ids_and_the_identifier_arguments():
    first = HistoryTurn(request_id="r-1", text='여기 사람들로 "Crazy Study" 스페이스 만들어', answer="만들었습니다.",
                        actions=(act("create_page", args={"title": "Crazy Study"}, refs={"id": SPACE}),
                                 act("add_page_label", args={"pageId": SPACE, "name": "tiffanie"}),
                                 act("add_page_label", args={"pageId": "f...", "name": "tanminkwan"}, ok_=False, code="TARGET_ERROR")))
    r = rig([say("네")], candidates=[], history=[first])
    await r.run("방금 만든 데 tanminkwan도 넣어", conversation_id="chat:room")
    asst = [m for m in r.request(0).messages if m.role == "assistant"][0].content
    assert asst == ("만들었습니다.\n[실행: workfluence__create_page ok → id=" + SPACE + ", "
                    "workfluence__add_page_label ok (pageId=" + SPACE + ", name=tiffanie), "
                    "workfluence__add_page_label TARGET_ERROR (pageId=f..., name=tanminkwan)]")


async def test_H2_free_text_arguments_stay_out_of_the_history_line():
    first = HistoryTurn(request_id="r-1", text="공지 써줘", answer="썼습니다.",
                        actions=(act("create_page", args={"title": "10월 회식 공지", "body": "강남역 18:30"}, refs={"id": "p-9"}),))
    r = rig([say("네")], candidates=[], history=[first])
    await r.run("그거 고쳐줘", conversation_id="c-1")
    asst = [m for m in r.request(0).messages if m.role == "assistant"][0].content
    assert asst.endswith("[실행: workfluence__create_page ok → id=p-9]") and "강남역" not in asst


async def test_H3_answer_none_from_a_limit_stop_still_keeps_the_ids():
    first = HistoryTurn(request_id="r-1", text="만들어", answer="", actions=(act("create_page", refs={"id": SPACE}),))
    r = rig([say("네")], candidates=[], history=[first])
    await r.run("이어서 해", conversation_id="c-1")
    asst = [m for m in r.request(0).messages if m.role == "assistant"][0].content
    assert SPACE in asst


# ---- 저장: refs가 기록과 이력에 남고, 예전 기록도 읽힌다 ----

def test_S1_refs_round_trip_through_the_store_and_old_rows_read_as_empty(tmp_path):
    db = SqliteInferenceDb(tmp_path / "i.db")
    db.create_running(request_id="r-1", agent_id="aiagent", requester="u", text="만들어", conversation_id="c-1", idempotency_key=None,
                      created_at="2026-10-16T00:00:00Z")
    db.finish_done("r-1", result("만들었습니다", actions=[act("create_page", refs={"id": SPACE})]), finished_at="2026-10-16T00:00:01Z",
                   elapsed_ms=10)
    assert db.get("r-1").actions[0].refs == {"id": SPACE}
    assert db.history("aiagent", "c-1", limit=5)[0].actions[0].refs == {"id": SPACE}
    assert act("x").to_dict()["refs"] == {}
    old = {"tool": "t", "revision": None, "ok": True, "errorCode": None, "targetRequestId": None, "write": False, "args": {}}
    assert ActionSummary.from_dict(old).refs == {}
