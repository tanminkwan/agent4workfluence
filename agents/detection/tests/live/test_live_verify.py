"""시험 스택 실호출 검증 (Phase 2 AC-15). A4W_LIVE_TESTS=1 이고 타겟 비밀이 있을 때만 돈다.

검증마다 시험 스페이스(VERIFY_SPACE_PREFIX + 검증 id)를 만들고 끝나면 지운다. admin 동작은 실행하지 않는다.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest

from a4w_core.errors import VerifyRequestInvalid

from ..verify_fakes import list_users, op

SECRETS_DIR = Path(os.environ.get("DETECTION_SECRETS_DIR", "/nonexistent"))

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("A4W_LIVE_TESTS") != "1", reason="A4W_LIVE_TESTS=1 일 때만"),
    pytest.mark.skipif(not (SECRETS_DIR / "workfluence.env").is_file(), reason="타겟 비밀이 없다"),
]

POLL_SECONDS = 2
WAIT_SECONDS = 300


@pytest.fixture(scope="module")
def context(tmp_path_factory):
    from a4w_detection.wiring import build_context

    ctx = build_context({
        "LOG_LEVEL": "INFO", "DETECTION_AUTH_MODE": "none",
        "DETECTION_SPEC_STORE_PATH": str(tmp_path_factory.mktemp("store")),
        "DETECTION_TARGETS_DIR": os.environ["DETECTION_TARGETS_DIR"],
        "DETECTION_TEXT_DIFF_CONTEXT_LINES": "3", "TARGETS": "workfluence",
        "DETECTION_SECRETS_DIR": str(SECRETS_DIR),
        "DETECTION_VERIFY_MAX_TOOLS": "100", "DETECTION_VERIFY_RESULT_DAYS": "30",
    })
    ctx.detect.detect("workfluence")  # 경로 검사에 쓸 현재 명세
    return ctx


def tools():
    read_page = op("read_page", "GET", "/pages/{id}", inputs={"pageId": "string"}, required=["pageId"],
                   bindings=[{"input": "pageId", "in": "path", "name": "id"}], fields=["id", "title", "currentVersionNo"])
    labels = op("list_page_labels", "GET", "/pages/{pageId}/labels", inputs={"pageId": "string"}, required=["pageId"],
                bindings=[{"input": "pageId", "in": "path", "name": "pageId"}], fields=["[].name"])
    add_comment = op("add_comment", "POST", "/pages/{pageId}/comments", inputs={"pageId": "string", "body": "string"},
                     required=["pageId", "body"],
                     bindings=[{"input": "pageId", "in": "path", "name": "pageId"},
                               {"input": "body", "in": "body", "pointer": "/body"}], fields=["id", "pageId"])
    add_comment["http"]["fixed"] = [{"in": "body", "pointer": "/format", "value": "markdown"}]
    update_page = op("update_page", "PATCH", "/pages/{id}",
                     inputs={"pageId": "string", "baseVersionNo": "integer", "body": "string"},
                     required=["pageId", "baseVersionNo", "body"],
                     bindings=[{"input": "pageId", "in": "path", "name": "id"},
                               {"input": "baseVersionNo", "in": "body", "pointer": "/baseVersionNo"},
                               {"input": "body", "in": "body", "pointer": "/body"}],
                     fields=["id", "currentVersionNo"])
    return [read_page, labels, add_comment, update_page]


SCENARIOS = {
    "add_comment": {"call": {"args": {"pageId": "${sandbox.pageId}", "body": "검증 댓글 ${run.id}"}},
                    "expect": {"ok": True, "fields": ["id"]}},
    "update_page": {"call": {"args": {"pageId": "${sandbox.pageId}", "baseVersionNo": "${sandbox.currentVersionNo}",
                                      "body": "검증으로 고친 본문 ${run.id}"}},
                    "expect": {"ok": True, "fields": ["currentVersionNo"]}},
}


def wait(ctx, vid):
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        rec = ctx.verify.get("workfluence", vid)
        if rec["status"] != "running":
            return rec
        time.sleep(POLL_SECONDS)
    pytest.fail("검증이 끝나지 않았다")


def test_profile_reads_token_scopes(context):
    p = context.profile.profile("workfluence")
    assert {"read", "write"} <= set(p["tokenScopes"])
    assert p["verification"]["available"]


def test_verification_runs_in_sandbox_and_cleans_up(context):
    vid = f"vf-live-{uuid.uuid4().hex[:8]}"
    rec, created = context.verify.submit({"targetId": "workfluence", "verificationId": vid, "tools": tools(),
                                          "references": [], "scenarios": SCENARIOS})
    assert created
    done = wait(context, vid)
    assert done["status"] == "done", done["error"]
    results = {r["tool"]: r for r in done["results"]}
    assert {k: v["result"] for k, v in results.items()} == {
        "read_page": "passed", "list_page_labels": "passed", "add_comment": "passed", "update_page": "passed",
    }, results
    assert done["sandbox"] == {"prepared": True, "cleanup": "ok", "cleanupFailures": []}


def test_admin_tool_is_rejected_before_any_call(context):
    with pytest.raises(VerifyRequestInvalid) as e:
        context.verify.submit({"targetId": "workfluence", "verificationId": f"vf-live-{uuid.uuid4().hex[:8]}",
                               "tools": [list_users()], "references": [], "scenarios": {}})
    assert {d["reason"] for d in e.value.details} == {"DENIED_SCOPE"}
