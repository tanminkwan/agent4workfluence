"""변경 반영 흐름 (Phase 2 설계 4절, AC-1·1a·3·3a·4·6·9). 외부는 가짜, 저장소는 실제."""

from __future__ import annotations

import copy

import pytest

from a4w_clients.rag.base import RagSpace
from a4w_manufacture.assist.assistant import AssistFailed
from a4w_manufacture.catalog import FileCatalog
from a4w_manufacture.errors import VerifyFailedAll
from a4w_manufacture.index.sync import IndexSync
from a4w_manufacture.service import ApplyService, TargetRuntime
from a4w_manufacture.storage.bundles import FileToolStore
from a4w_manufacture.storage.sqlite_db import SqliteManufactureDb
from a4w_manufacture.transform.openapi3 import OpenApi3Transformer

from ..apply_fakes import AUTHORING, OPS, FakeAssistant, FakeDetection, FakeRag, changeset, frags, write_catalog

DEFAULT_GROUPS = ("read", "history", "communication", "authoring")


class Env:
    def __init__(self, tmp_path, groups=DEFAULT_GROUPS, scopes=frozenset({"read", "write", "admin"}), outputs=None):
        self.catalog_dir = write_catalog(tmp_path / "catalog")
        self.detection = FakeDetection(scopes)
        self.assistant = FakeAssistant(outputs)
        self.store = FileToolStore(tmp_path / "store")
        self.db = SqliteManufactureDb(tmp_path / "store" / "manufacture.db")
        self.rag = FakeRag()
        self.ticks = 0
        self.groups = groups
        self.deploy()

    def deploy(self):
        """서비스를 (다시) 띄운다. catalog는 띄울 때 읽는다(이미지에 들어 있다)."""
        groups = self.groups
        rt = TargetRuntime(target_id="wf", catalog=FileCatalog(self.catalog_dir), enabled_groups=tuple(groups),
                           transformer=OpenApi3Transformer(content_types=["application/json"]), max_chars=4000,
                           no_scenario="pending",
                           rag_space=RagSpace("a4w_tools_wf", "a4w-tools-wf", "wf tools"))
        self.service = ApplyService(
            targets={"wf": rt}, detection=self.detection, assistant=self.assistant, store=self.store, db=self.db,
            index=IndexSync(self.rag, self.db, content_max_chars=600, list_limit=100, clock=self.now),
            clock=self.now, llm_max_calls=10, example_count=2,
        )

    def now(self):
        self.ticks += 1
        return f"2026-10-13T00:00:{self.ticks:02d}Z"

    def apply(self, cs, apply_id):
        # 실제로는 ApplyRunner가 접수 때 apply 행을 만든다
        self.db.create_apply({"applyId": apply_id, "targetId": "wf", "changeSetId": cs.change_set_id if cs else None,
                              "status": "running", "startedAt": self.now()})
        return self.service.run("wf", apply_id=apply_id, changeset=cs)

    def served(self):
        v = self.store.current("wf")
        return self.store.read("wf", v).tools if v else {}


def first(env, ops=OPS, to="v1"):
    return env.apply(changeset("cs-1", {}, frags(*ops), None, to), "ap-1")


def outcome(result, op_id):
    return next(i for i in result["items"] if i.get("operationId") == op_id)


# ── AC-1: 첫 반영은 seed 그대로, LLM 없음

def test_first_apply_uses_seed_without_llm(tmp_path):
    env = Env(tmp_path)
    r = first(env)
    assert r["status"] == "applied"
    assert env.assistant.calls == []
    assert set(env.served()) == {"add_comment", "read_page", "search_pages", "comment_on_page_by_title"}
    assert outcome(r, "comments.create") == {"operationId": "comments.create", "method": "POST",
                                             "path": "/pages/{pageId}/comments", "tool": "add_comment",
                                             "outcome": "added", "origin": "seed",
                                             "verify": {"result": "passed", "calls": 1}}
    assert outcome(r, "users.list")["outcome"] == "excluded" and outcome(r, "users.list")["reason"] == "ADMIN"
    assert outcome(r, "pages.delete")["reason"] == "GROUP_DISABLED"
    assert outcome(r, "chat.send")["outcome"] == "excluded" and outcome(r, "chat.send")["reason"] == "NOT_NEEDED"
    assert not r.get("codeOwnedChanges")                    # 채팅 API 변경은 따로 알리지 않는다
    assert r["summary"]["added"] == 4 and r["summary"]["excluded"] == 3 and r["summary"]["codeOwned"] == 0
    assert r["toolVersion"] == env.store.current("wf")
    assert r["index"]["upserted"] == 4 and not r["index"]["drift"]
    call = env.detection.verify_calls[0]
    assert call["id"] == "vf-ap-1" and "add_comment" in call["scenarios"]
    st = env.db.state("wf")
    assert st.last_applied_spec_version == "v1" and st.head_tool_version == r["toolVersion"]


def test_seed_served_definitions_use_context_and_hide_inputs(tmp_path):
    env = Env(tmp_path)
    first(env)
    search = env.served()["search_pages"]
    assert list(search["inputSchema"]["properties"]) == ["q", "space"]          # space는 선택 칸 (설계 26절)
    assert search["inputSchema"]["required"] == ["q"]
    assert {"input": "space", "in": "query", "name": "space", "contextDefault": "homeSpace"} in search["http"]["bindings"]
    assert search["http"]["omitted"] == ["limit"]


def test_composite_risk_and_scopes_are_computed(tmp_path):
    env = Env(tmp_path)
    first(env)
    comp = env.served()["comment_on_page_by_title"]
    assert comp["risk"] == "write" and comp["scopes"] == ["read", "write"]


# ── AC-1a: 바뀐 동작만 LLM, 이름 유지. Workfluence는 구조 변경이 needsReview라 보류

def modified_comments():
    f = frags(*OPS)
    schema = f["comments.create"]["requestBody"]["content"]["application/json"]["schema"]
    schema["properties"]["mention"] = {"type": "string", "description": "멘션할 사용자"}
    return f


def test_modified_operation_goes_through_llm_and_pending(tmp_path):
    out = copy.deepcopy(AUTHORING["comments.create"])
    out["inputs"]["mention"] = {"description": "멘션할 사용자", "hide": True}
    env = Env(tmp_path, outputs={"comments.create": out})
    r1 = first(env)
    r2 = env.apply(changeset("cs-2", frags(*OPS), modified_comments(), "v1", "v2"), "ap-2")
    assert env.assistant.calls == [("update", "comments.create", "add_comment")]
    item = outcome(r2, "comments.create")
    assert item["outcome"] == "pending" and r2["status"] == "partial"
    assert r2["pendingReview"][0]["reasons"] == ["SPEC_REVIEW"]
    assert r2["pendingReview"][0]["tool"] == "add_comment"
    assert env.store.current("wf") == r1["toolVersion"]  # 승인 전까지 이전 정의 그대로
    assert env.db.state("wf").last_applied_spec_version == "v2"


def test_approved_pending_is_applied_by_cleanup_apply(tmp_path):
    out = copy.deepcopy(AUTHORING["comments.create"])
    out["inputs"]["mention"] = {"description": "멘션할 사용자", "hide": True}
    env = Env(tmp_path, outputs={"comments.create": out})
    first(env)
    r2 = env.apply(changeset("cs-2", frags(*OPS), modified_comments(), "v1", "v2"), "ap-2")
    rid = r2["pendingReview"][0]["reviewId"]
    env.db.decide_review(rid, decision="approved", by="me", note=None, at="t")
    r3 = env.apply(None, "ap-3")
    assert outcome(r3, "comments.create")["outcome"] == "updated"
    assert env.served()["add_comment"]["source"]["fragmentSha256"] != r2["toolVersion"]
    assert env.db.review(rid)["status"] == "applied"
    assert env.apply(None, "ap-4")["status"] == "noop"


def test_doc_only_change_redescribes_without_review(tmp_path):
    env = Env(tmp_path, outputs={"pages.get": AUTHORING["pages.get"]})
    first(env)
    after = frags(*OPS)
    after["pages.get"]["description"] = "설명 문구만 바뀜"
    r = env.apply(changeset("cs-2", frags(*OPS), after, "v1", "v2"), "ap-2")
    assert env.assistant.calls == [("redescribe", "pages.get", "read_page")]
    assert outcome(r, "pages.get")["outcome"] == "updated" and outcome(r, "pages.get")["origin"] == "llm-doc"
    assert r["status"] == "applied"


# ── AC-3: 추가된 동작만 tool로 더하고 기존 이름은 그대로

def test_added_operation_is_created_by_llm(tmp_path):
    out = {"name": "list_comments", "title": "댓글 목록", "description": "페이지의 댓글을 본다.",
           "inputs": {"pageId": {"description": "search_pages 결과의 pageId"}},
           "index": {"summary": "댓글 목록", "examples": ["댓글 보여줘", "의견 목록", "달린 댓글"]}}
    env = Env(tmp_path, outputs={"comments.list": out})
    r1 = first(env)
    before_names = set(env.served())
    r2 = env.apply(changeset("cs-2", frags(*OPS), frags(*OPS, "comments.list"), "v1", "v2"), "ap-2")
    assert env.assistant.calls == [("create", "comments.list", None)]
    assert outcome(r2, "comments.list")["outcome"] == "added"
    assert set(env.served()) == before_names | {"list_comments"}
    assert {n: t["revision"] for n, t in env.served().items() if n in before_names} == \
        {n: t["revision"] for n, t in env.store.read("wf", r1["toolVersion"]).tools.items()}


# ── AC-3a: 채팅 동작은 tool이 아니고, 추가돼도 아무것도 바뀌지 않는다(알림도 없다)

def test_added_chat_operations_change_nothing(tmp_path):
    env = Env(tmp_path)
    first(env, ops=[o for o in OPS if o != "chat.send"])
    r = env.apply(changeset("cs-2", frags(*[o for o in OPS if o != "chat.send"]), frags(*OPS), "v1", "v2"), "ap-2")
    assert not r.get("codeOwnedChanges")
    assert r["status"] == "noop" and r["toolVersion"] == env.store.current("wf")


# ── AC-4: 삭제·위험 동작은 보류, 나머지는 반영

def test_removed_operation_is_pending_until_approved(tmp_path):
    env = Env(tmp_path)
    first(env)
    after = frags(*[o for o in OPS if o != "pages.get"])
    r2 = env.apply(changeset("cs-2", frags(*OPS), after, "v1", "v2"), "ap-2")
    assert outcome(r2, "pages.get")["outcome"] == "pending"
    assert r2["pendingReview"][0]["reasons"] == ["TOOL_REMOVED"]
    assert "read_page" in env.served()
    env.db.decide_review(r2["pendingReview"][0]["reviewId"], decision="approved", by="me", note=None, at="t")
    r3 = env.apply(None, "ap-3")
    assert outcome(r3, "pages.get")["outcome"] == "removed"
    assert "read_page" not in env.served()


def test_danger_tool_is_verified_then_pending(tmp_path):
    env = Env(tmp_path, groups=DEFAULT_GROUPS + ("danger",))
    r = first(env)
    item = outcome(r, "pages.delete")
    assert item["outcome"] == "pending" and item["verify"]["result"] == "passed"
    assert "delete_page" not in env.served()
    assert r["pendingReview"][0]["reasons"] == ["DANGER_ADDED"]


def test_rejected_review_is_not_asked_again(tmp_path):
    env = Env(tmp_path, groups=DEFAULT_GROUPS + ("danger",))
    r = first(env)
    env.db.decide_review(r["pendingReview"][0]["reviewId"], decision="rejected", by="me", note="no", at="t")
    r2 = env.apply(None, "ap-2")
    assert r2["status"] == "noop" and r2["pendingReview"] == []


# ── AC-9: 검증을 통과한 tool만 들어간다

def test_verification_failure_keeps_tool_out_and_retries_next_time(tmp_path):
    env = Env(tmp_path)
    env.detection.fail = {"read_page"}
    r = first(env)
    assert outcome(r, "pages.get")["outcome"] == "failed"
    assert outcome(r, "pages.get")["reason"] == "VERIFY_FAILED"
    assert "read_page" not in env.served() and r["status"] == "partial"
    env.detection.fail = set()
    r2 = env.apply(None, "ap-2")
    assert outcome(r2, "pages.get")["outcome"] == "added"
    assert "read_page" in env.served()


def test_unverified_tool_follows_policy(tmp_path):
    env = Env(tmp_path)
    env.detection.unverified = {"search_pages"}
    r = first(env)
    assert outcome(r, "search.find")["outcome"] == "pending"
    assert r["pendingReview"][0]["reasons"] == ["UNVERIFIED"]
    # 상위 tool은 참조 tool이 없으니 들어가지 않는다
    assert "comment_on_page_by_title" not in env.served()


def test_llm_failure_marks_failed_and_others_continue(tmp_path):
    env = Env(tmp_path, outputs={"comments.list": AssistFailed("LLM_TIMEOUT", "x")})
    first(env)
    r = env.apply(changeset("cs-2", frags(*OPS), frags(*OPS, "comments.list"), "v1", "v2"), "ap-2")
    assert outcome(r, "comments.list")["outcome"] == "failed" and outcome(r, "comments.list")["reason"] == "LLM_TIMEOUT"
    assert r["status"] == "partial"


# ── AC-6: 도중 실패하면 포인터가 그대로

def test_verification_failed_all_fails_apply_and_changes_nothing(tmp_path):
    out = {"name": "list_comments", "title": "댓글 목록", "description": "페이지의 댓글을 본다.",
           "inputs": {"pageId": {"description": "search_pages 결과의 pageId"}},
           "index": {"summary": "댓글 목록", "examples": ["댓글 보여줘", "의견 목록", "달린 댓글"]}}
    env = Env(tmp_path, outputs={"comments.list": out})
    r1 = first(env)
    env.detection.fail_all = True
    with pytest.raises(VerifyFailedAll):
        env.apply(changeset("cs-2", frags(*OPS), frags(*OPS, "comments.list"), "v1", "v2"), "ap-2")
    assert env.store.current("wf") == r1["toolVersion"]
    assert env.db.state("wf").last_applied_spec_version == "v1"
    assert ("GET", "/pages/{pageId}/comments") not in env.db.operations("wf")


def test_rollback_moves_pointer_and_next_apply_builds_on_head(tmp_path):
    out = {"name": "list_comments", "title": "댓글 목록", "description": "페이지의 댓글을 본다.",
           "inputs": {"pageId": {"description": "search_pages 결과의 pageId"}},
           "index": {"summary": "댓글 목록", "examples": ["댓글 보여줘", "의견 목록", "달린 댓글"]}}
    env = Env(tmp_path, outputs={"comments.list": out})
    r1 = first(env)
    r2 = env.apply(changeset("cs-2", frags(*OPS), frags(*OPS, "comments.list"), "v1", "v2"), "ap-2")
    rb = env.service.rollback("wf", r1["toolVersion"])
    assert rb["currentToolVersion"] == r1["toolVersion"] and rb["headToolVersion"] == r2["toolVersion"]
    assert "list_comments" not in env.served()
    env.db.decide_review  # noqa: B018
    after = frags(*OPS, "comments.list")
    after["pages.get"]["description"] = "바뀜"
    env.assistant.outputs["pages.get"] = AUTHORING["pages.get"]
    r3 = env.apply(changeset("cs-3", frags(*OPS, "comments.list"), after, "v2", "v3"), "ap-3")
    assert "list_comments" in env.served()  # head(v2) 위에 쌓였다
    assert env.store.read("wf", r3["toolVersion"]).manifest["parent"] == r2["toolVersion"]


def test_scope_missing_excludes_write_tools(tmp_path):
    env = Env(tmp_path, scopes=frozenset({"read"}))
    r = first(env)
    assert outcome(r, "comments.create")["reason"] == "SCOPE_MISSING"
    assert "add_comment" not in env.served()
    assert "comment_on_page_by_title" not in env.served()


def test_scenario_setup_tools_are_sent_as_references_or_scenario_dropped(tmp_path):
    import yaml

    env = Env(tmp_path)
    first(env)
    sc_dir = env.catalog_dir / "scenarios"
    (sc_dir / "list_comments.yaml").write_text(yaml.safe_dump({
        "setup": [{"tool": "add_comment", "args": {"pageId": "${sandbox.pageId}", "body": "x"}}],
        "call": {"args": {"pageId": "${sandbox.pageId}"}}}))
    (sc_dir / "list_page_labels.yaml").write_text(yaml.safe_dump({
        "setup": [{"tool": "ghost_tool", "args": {}}], "call": {"args": {"pageId": "${sandbox.pageId}"}}}))
    out_c = {"name": "list_comments", "title": "댓글 목록", "description": "댓글을 본다.",
             "inputs": {"pageId": {"description": "pageId"}},
             "index": {"summary": "댓글 목록", "examples": ["댓글 보여줘", "의견 목록", "달린 댓글"]}}
    out_l = {"name": "list_page_labels", "title": "라벨 목록", "description": "라벨을 본다.",
             "inputs": {"pageId": {"description": "pageId"}},
             "index": {"summary": "라벨 목록", "examples": ["라벨 보여줘", "태그 목록", "붙은 라벨"]}}
    env.assistant.outputs.update({"comments.list": out_c, "labels.forPage": out_l})
    env.service.targets["wf"] = env.service.targets["wf"].__class__(
        **{**env.service.targets["wf"].__dict__, "catalog": type(env.service.targets["wf"].catalog)(env.catalog_dir)})
    env.apply(changeset("cs-2", frags(*OPS), frags(*OPS, "comments.list", "labels.forPage"), "v1", "v2"), "ap-2")
    call = env.detection.verify_calls[-1]
    assert "add_comment" in call["references"]
    assert "list_comments" in call["scenarios"] and "list_page_labels" not in call["scenarios"]


def test_unverified_pending_is_reverified_when_scenario_appears(tmp_path):
    """검증 안 됨(UNVERIFIED)만으로 보류된 항목은 다음 반영 때 다시 검증한다 (시나리오가 생겼을 수 있다)."""
    env = Env(tmp_path)
    env.detection.unverified = {"search_pages"}
    r1 = first(env)
    assert r1["pendingReview"][0]["reasons"] == ["UNVERIFIED"]
    env.detection.unverified = set()
    r2 = env.apply(None, "ap-2")
    assert outcome(r2, "search.find")["outcome"] == "added"
    assert "search_pages" in env.served() and "comment_on_page_by_title" in env.served()
    assert env.db.review(r1["pendingReview"][0]["reviewId"])["status"] == "superseded"
    assert r2["pendingReview"] == []


# ── 설계 24.2: seed 글만 바뀌면 서빙 중인 seed tool을 seed로 다시 만든다 (LLM 없음)

def edit_seed(env, name, **index):
    import json

    from a4w_manufacture.assembly import revision_of
    path = env.catalog_dir / "seed" / "tools" / f"{name}.json"
    d = json.loads(path.read_text(encoding="utf-8"))
    d["index"].update(index)
    d["revision"] = revision_of(d)
    path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    env.deploy()                                       # 바뀐 seed는 새 이미지로 배포되어야 읽힌다
    return d


def test_changed_seed_text_refreshes_the_served_tool_without_llm(tmp_path):
    env = Env(tmp_path)
    first(env)
    before = env.served()["read_page"]["revision"]
    examples = ["문서 본문 읽어줘", "그 글 열어줘", "Read the page"]
    edit_seed(env, "read_page", examples=examples)
    r = env.apply(None, "ap-2")
    assert r["status"] == "applied"
    o = outcome(r, "pages.get")
    assert o["outcome"] == "updated" and o["origin"] == "seed"
    served = env.served()["read_page"]
    assert served["index"]["examples"] == examples and served["revision"] != before
    assert all(i.get("operationId") != "comments.create" for i in r["items"])     # 그대로인 것은 목록에 없다
    assert env.assistant.calls == []


def test_unchanged_seed_leaves_the_served_tool_unchanged(tmp_path):
    env = Env(tmp_path)
    first(env)
    before = env.served()
    r = env.apply(None, "ap-2")
    assert not any(i["outcome"] in ("updated", "added") for i in r["items"])
    assert env.served() == before


def test_an_invalid_changed_seed_keeps_the_served_tool_and_says_why(tmp_path):
    env = Env(tmp_path)
    first(env)
    before = env.served()["read_page"]
    edit_seed(env, "read_page", examples=["하나"])                    # 하한(3) 미만
    r = env.apply(None, "ap-2")
    o = outcome(r, "pages.get")
    assert o["outcome"] == "unchanged" and o["reason"] == "SEED_INVALID"
    assert env.served()["read_page"] == before


# ── 설계 25.1: 명세가 바뀐 동작도 새 명세에 맞는 seed가 있으면 LLM 대신 seed로 (보류는 그대로)

def test_spec_change_with_a_matching_seed_uses_the_seed_and_still_waits_for_review(tmp_path):
    import json

    from ..apply_fakes import seed_definition
    env = Env(tmp_path)
    first(env)
    after = modified_comments()
    d = seed_definition("comments.create", after["comments.create"])
    (env.catalog_dir / "seed" / "tools" / "add_comment.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    env.deploy()
    r = env.apply(changeset("cs-2", frags(*OPS), after, "v1", "v2"), "ap-2")
    assert env.assistant.calls == []
    item = outcome(r, "comments.create")
    assert item["outcome"] == "pending" and item["origin"] == "seed"
    assert r["pendingReview"][0]["reasons"] == ["SPEC_REVIEW"]


def test_spec_change_without_a_matching_seed_still_goes_through_llm(tmp_path):
    out = copy.deepcopy(AUTHORING["comments.create"])
    out["inputs"]["mention"] = {"description": "멘션할 사용자", "hide": True}
    env = Env(tmp_path, outputs={"comments.create": out})
    first(env)
    env.apply(changeset("cs-2", frags(*OPS), modified_comments(), "v1", "v2"), "ap-2")
    assert env.assistant.calls == [("update", "comments.create", "add_comment")]
