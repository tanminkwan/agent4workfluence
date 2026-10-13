"""받은 정의 검사 (Phase 2 설계 11.4절, FR-334)."""

from __future__ import annotations

from a4w_detection.verify.guard import check_request

from ..verify_fakes import CONTEXT, SPEC_OPS, add_comment, comment_by_title, list_users, read_page, search_pages


def check(tools, scenarios=None, references=(), max_tools=10):
    return check_request(
        tools=list(tools), references=list(references), scenarios=scenarios or {},
        spec_operations=SPEC_OPS, deny_scopes={"admin"}, max_tools=max_tools, context_keys=set(CONTEXT),
    )


def reasons(problems):
    return {(p["tool"], p["reason"]) for p in problems}


def test_valid_request_passes():
    sc = {"add_comment": {"call": {"args": {"pageId": "${sandbox.pageId}", "body": "x"}}}}
    assert check([read_page(), add_comment(), search_pages(), comment_by_title()], sc) == []


def test_schema_invalid():
    bad = read_page()
    bad["name"] = "Bad"
    assert ("Bad", "SCHEMA_INVALID") in reasons(check([bad]))


def test_denied_scope():
    assert ("list_users", "DENIED_SCOPE") in reasons(check([list_users()]))


def test_operation_not_in_current_spec():
    t = read_page()
    t["http"]["path"] = "/pages/{id}/secret"
    assert ("read_page", "UNKNOWN_OPERATION") in reasons(check([t]))


def test_composite_reference_must_be_present():
    assert ("comment_on_page_by_title", "REFERENCE_INVALID") in reasons(check([comment_by_title()]))


def test_references_satisfy_composite():
    assert check([comment_by_title()], references=[search_pages(), add_comment()]) == []


def test_write_scenario_must_target_sandbox():
    sc = {"add_comment": {"call": {"args": {"pageId": "p-of-someone", "body": "x"}}}}
    assert ("add_comment", "WRITE_OUTSIDE_SANDBOX") in reasons(check([add_comment()], sc))


def test_write_scenario_saved_value_is_allowed():
    sc = {"add_comment": {"setup": [{"tool": "read_page", "args": {"pageId": "${sandbox.pageId}"}, "save": {"pid": "/id"}}],
                          "call": {"args": {"pageId": "${saved.pid}", "body": "x"}}}}
    assert check([add_comment()], sc, references=[read_page()]) == []


def test_composite_write_scenario_path_inputs_checked():
    # 상위 tool의 pageId는 앞 step에서 오므로 경로 칸에 직접 매핑되는 입력이 없다 → 통과
    sc = {"comment_on_page_by_title": {"call": {"args": {"title": "${sandbox.pageTitle}", "body": "x"}}}}
    assert check([comment_by_title()], sc, references=[search_pages(), add_comment()]) == []


def test_unknown_sandbox_key():
    sc = {"read_page": {"call": {"args": {"pageId": "${sandbox.nope}"}}}}
    assert ("read_page", "SCENARIO_UNKNOWN_KEY") in reasons(check([read_page()], sc))


def test_scenario_for_tool_not_in_request():
    sc = {"ghost_tool": {"call": {"args": {}}}}
    assert ("ghost_tool", "SCENARIO_UNKNOWN_TOOL") in reasons(check([read_page()], sc))


def test_too_many_tools():
    assert ("*", "TOO_MANY_TOOLS") in reasons(check([read_page(), search_pages()], max_tools=1))
