"""검증 실행: 순서, 판정, 정리 (Phase 2 설계 11.1·11.3절)."""

from __future__ import annotations

import pytest

from a4w_core.errors import SandboxUnavailable
from a4w_detection.verify.runner import VerificationRunner

from ..verify_fakes import FakeSandbox, add_comment, comment_by_title, make_runtime_parts, read_page, search_pages


def runner(sandbox=None, target=None):
    target, executor, caller = make_runtime_parts(target)
    sandbox = sandbox or FakeSandbox()
    return VerificationRunner(executor=executor, caller=caller, sandbox=sandbox), target, sandbox


def by_tool(out):
    return {r["tool"]: r for r in out["results"]}


def test_scenarios_and_auto_scenarios_pass():
    r, target, sandbox = runner()
    out = r.run("vf-1", tools=[read_page(), add_comment(), search_pages()], references=[],
                scenarios={"add_comment": {"call": {"args": {"pageId": "${sandbox.pageId}", "body": "검증 ${run.id}"}},
                                           "expect": {"ok": True, "fields": ["id"]}}})
    res = by_tool(out)
    assert res["read_page"]["result"] == "passed"      # 자동 시나리오
    assert res["search_pages"]["result"] == "passed"   # 자동 시나리오 (q="a4w")
    assert res["add_comment"]["result"] == "passed"
    assert out["sandbox"] == {"prepared": True, "cleanup": "ok", "cleanupFailures": []}
    assert sandbox.prepared == ["vf-1"] and sandbox.cleaned == 1


def test_reads_run_before_writes_before_composites():
    r, target, _ = runner()
    r.run("vf-1", tools=[comment_by_title(), add_comment(), read_page(), search_pages()], references=[],
          scenarios={"add_comment": {"call": {"args": {"pageId": "${sandbox.pageId}", "body": "x"}}},
                     "comment_on_page_by_title": {"call": {"args": {"title": "${sandbox.pageTitle}", "body": "y"}}}})
    methods = [m for m, _ in target.calls]
    first_post = methods.index("POST")
    assert all(m == "GET" for m in methods[:first_post])
    assert target.calls[-1] == ("POST", "/pages/p1/comments")  # 상위 tool이 마지막


def test_expected_field_missing_fails():
    r, _, _ = runner()
    out = r.run("vf-1", tools=[read_page()], references=[],
                scenarios={"read_page": {"call": {"args": {"pageId": "${sandbox.pageId}"}},
                                         "expect": {"ok": True, "fields": ["body"]}}})  # 가공으로 body가 빠진다
    res = by_tool(out)["read_page"]
    assert res["result"] == "failed" and res["reason"] == "EXPECT_FIELD_MISSING"


def test_target_error_fails_with_details():
    r, _, _ = runner()
    out = r.run("vf-1", tools=[read_page()], references=[],
                scenarios={"read_page": {"call": {"args": {"pageId": "nope"}}}})
    res = by_tool(out)["read_page"]
    assert res["result"] == "failed"
    assert res["error"]["targetCode"] == "NOT_FOUND"
    assert res["error"]["requestId"] == "rq"


def test_expected_error_passes():
    r, _, _ = runner()
    out = r.run("vf-1", tools=[read_page()], references=[],
                scenarios={"read_page": {"call": {"args": {"pageId": "nope"}}, "expect": {"ok": False, "error": "NOT_FOUND"}}})
    assert by_tool(out)["read_page"]["result"] == "passed"


def test_write_without_scenario_is_unverified():
    r, target, _ = runner()
    out = r.run("vf-1", tools=[add_comment()], references=[], scenarios={})
    res = by_tool(out)["add_comment"]
    assert res["result"] == "unverified" and res["reason"] == "NO_SCENARIO"
    assert target.calls == []


def test_composite_skipped_when_referenced_tool_failed():
    target_parts = make_runtime_parts()
    target = target_parts[0]
    target.search_items = []  # search는 성공하지만 상위 tool의 expect가 실패한다
    r, _, _ = runner(target=target)
    out = r.run("vf-1", tools=[add_comment(), comment_by_title()], references=[search_pages()],
                scenarios={"add_comment": {"call": {"args": {"pageId": "bad", "body": "x"}}},  # 실패
                           "comment_on_page_by_title": {"call": {"args": {"title": "t", "body": "y"}}}})
    res = by_tool(out)
    assert res["add_comment"]["result"] == "failed"
    assert res["comment_on_page_by_title"]["result"] == "skipped"


def test_setup_and_saved_values():
    r, _, _ = runner()
    sc = {"add_comment": {"setup": [{"tool": "read_page", "args": {"pageId": "${sandbox.pageId}"}, "save": {"pid": "/id"}}],
                          "call": {"args": {"pageId": "${saved.pid}", "body": "x"}}, "save": {"cid": "/id"}}}
    out = r.run("vf-1", tools=[add_comment()], references=[read_page()], scenarios=sc)
    res = by_tool(out)["add_comment"]
    assert res["result"] == "passed"
    assert res["calls"] == 2


def test_cleanup_failure_is_reported():
    r, _, _ = runner(sandbox=FakeSandbox(fail_cleanup=True))
    out = r.run("vf-1", tools=[read_page()], references=[], scenarios={})
    assert out["sandbox"]["cleanup"] == "failed"
    assert out["sandbox"]["cleanupFailures"] == ["삭제 실패"]


def test_prepare_failure_raises():
    r, _, _ = runner(sandbox=FakeSandbox(fail_prepare=True))
    with pytest.raises(SandboxUnavailable):
        r.run("vf-1", tools=[read_page()], references=[], scenarios={})


def test_cleanup_runs_even_if_a_tool_crashes(monkeypatch):
    r, _, sandbox = runner()

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(r, "_run_one", boom)
    with pytest.raises(RuntimeError):
        r.run("vf-1", tools=[read_page()], references=[], scenarios={})
    assert sandbox.cleaned == 1


def test_sandbox_values_are_the_execution_context():
    """맥락 키(homeSpace)로 고정된 칸은 검증 때 시험 환경 값으로 채워진다 (Phase 2 설계 6.3.1)."""
    r, target, _ = runner()
    t = search_pages()
    t["http"]["fixed"] = [{"in": "query", "name": "space", "context": "homeSpace"}]
    out = r.run("vf-1", tools=[t], references=[], scenarios={})
    assert by_tool(out)["search_pages"]["result"] == "passed"
