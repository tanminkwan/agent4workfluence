"""manufacture API 전체 (Phase 2 설계 16절, AC-5): 202·조회·멱등·순서·잠금·보류 승인·되돌리기·오류 모양."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from a4w_clients.rag.base import RagSpace
from a4w_core.locking import FileTargetLock
from a4w_manufacture.app import AppContext, create_app
from a4w_manufacture.catalog import FileCatalog
from a4w_manufacture.errors import ApplyInProgress
from a4w_manufacture.index.sync import IndexSync
from a4w_manufacture.reviews import ReviewService
from a4w_manufacture.runner import ApplyRunner
from a4w_manufacture.service import ApplyService, TargetRuntime
from a4w_manufacture.storage.bundles import FileToolStore
from a4w_manufacture.storage.sqlite_db import SqliteManufactureDb
from a4w_manufacture.toolsets import ToolsetQuery
from a4w_manufacture.transform.openapi3 import OpenApi3Transformer

from ..apply_fakes import OPS, FakeAssistant, FakeDetection, FakeRag, changeset, frags, write_catalog


class ChangeSets(FakeDetection):
    def __init__(self, groups_danger=False):
        super().__init__()
        self.sets = {}

    def changeset(self, target_id, cs_id):
        from a4w_core.errors import ChangeSetNotFound

        if cs_id not in self.sets:
            raise ChangeSetNotFound(cs_id)
        return self.sets[cs_id]


class ManualStarter:
    def __init__(self):
        self.jobs = []

    def __call__(self, job):
        self.jobs.append(job)

    def run_all(self):
        while self.jobs:
            self.jobs.pop(0)()


def build(tmp_path, groups=("read", "history", "communication", "authoring"), auth="none"):
    det = ChangeSets()
    det.sets["cs-1"] = changeset("cs-1", {}, frags(*OPS), None, "v1")
    det.sets["cs-2"] = changeset("cs-2", frags(*OPS), frags(*[o for o in OPS if o != "pages.get"]), "v1", "v2")
    det.sets["cs-x"] = changeset("cs-x", frags(*OPS), frags(*OPS), "v9", "v10")
    store = FileToolStore(tmp_path / "store")
    db = SqliteManufactureDb(tmp_path / "store" / "manufacture.db")
    ticks = iter(range(10_000))

    def clock():
        return f"2026-10-13T00:{next(ticks) // 60:02d}:{next(ticks) % 60:02d}Z"

    rt = TargetRuntime(target_id="wf", catalog=FileCatalog(write_catalog(tmp_path / "catalog")),
                       enabled_groups=groups, transformer=OpenApi3Transformer(content_types=["application/json"]),
                       max_chars=4000, no_scenario="pending", rag_space=RagSpace("a4w_tools_wf", "a4w-tools-wf", "d"))
    service = ApplyService(targets={"wf": rt}, detection=det, assistant=FakeAssistant(), store=store, db=db,
                           index=IndexSync(FakeRag(), db, content_max_chars=600, list_limit=100, clock=clock),
                           clock=clock, llm_max_calls=10, example_count=2)
    starter = ManualStarter()
    ids = iter(f"ap-{i}" for i in range(1, 100))
    runner = ApplyRunner(service=service, changesets=det, db=db,
                         lock=FileTargetLock(tmp_path / "store" / "locks", busy=lambda k: ApplyInProgress(k)),
                         clock=clock, new_id=lambda: next(ids), starter=starter)
    ctx = AppContext(runner=runner, reviews=ReviewService(db, clock=clock), service=service, store=store, db=db,
                     toolsets=ToolsetQuery(targets=["wf"], store=store), auth_mode=auth, api_token="tk" if auth == "bearer" else None)
    return TestClient(create_app(ctx), raise_server_exceptions=False), starter, det


def test_apply_202_then_result(tmp_path):
    c, starter, _ = build(tmp_path)
    r = c.post("/v1/apply", json={"targetId": "wf", "changeSetId": "cs-1"})
    assert r.status_code == 202
    body = r.json()
    assert body == {"status": "running", "applyId": "ap-1", "targetId": "wf", "changeSetId": "cs-1"}
    assert c.get("/v1/applies/ap-1").json()["status"] == "running"
    starter.run_all()
    g = c.get("/v1/applies/ap-1").json()
    assert g["status"] == "applied" and g["toolVersion"].startswith("tv-000001-")
    assert g["summary"]["added"] == 4


def test_same_changeset_returns_previous_result(tmp_path):
    c, starter, _ = build(tmp_path)
    c.post("/v1/apply", json={"targetId": "wf", "changeSetId": "cs-1"})
    starter.run_all()
    r = c.post("/v1/apply", json={"targetId": "wf", "changeSetId": "cs-1"})
    assert r.status_code == 200 and r.json()["applyId"] == "ap-1"
    assert starter.jobs == []


def test_out_of_order_is_409_with_versions(tmp_path):
    c, starter, _ = build(tmp_path)
    r = c.post("/v1/apply", json={"targetId": "wf", "changeSetId": "cs-2"})  # 첫 반영 전에 v1→v2
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "CHANGESET_OUT_OF_ORDER"
    assert err["details"] == {"lastAppliedSpecVersion": None, "changeSetFromVersion": "v1"}


def test_in_progress_is_409_with_apply_id(tmp_path):
    c, starter, _ = build(tmp_path)
    c.post("/v1/apply", json={"targetId": "wf", "changeSetId": "cs-1"})
    r = c.post("/v1/apply", json={"targetId": "wf"})
    assert r.status_code == 409
    assert r.json()["error"] == {"code": "APPLY_IN_PROGRESS", "message": r.json()["error"]["message"],
                                 "details": {"applyId": "ap-1"}}
    starter.run_all()
    assert c.post("/v1/apply", json={"targetId": "wf"}).status_code == 202


@pytest.mark.parametrize("payload,status,code", [
    ({"targetId": "nope", "changeSetId": "cs-1"}, 404, "TARGET_NOT_FOUND"),
    ({"targetId": "wf", "changeSetId": "cs-none"}, 404, "CHANGESET_NOT_FOUND"),
    ({"targetId": "wf", "extra": 1}, 400, "VALIDATION_ERROR"),
    ({}, 400, "VALIDATION_ERROR"),
])
def test_rejections(tmp_path, payload, status, code):
    c, _, _ = build(tmp_path)
    r = c.post("/v1/apply", json=payload)
    assert r.status_code == status and r.json()["error"]["code"] == code


def test_failed_apply_is_recorded_and_can_rerun(tmp_path):
    c, starter, det = build(tmp_path)
    det.fail_all = True
    c.post("/v1/apply", json={"targetId": "wf", "changeSetId": "cs-1"})
    starter.run_all()
    g = c.get("/v1/applies/ap-1").json()
    assert g["status"] == "failed" and g["error"]["code"] == "VERIFY_FAILED_ALL"
    det.fail_all = False
    r = c.post("/v1/apply", json={"targetId": "wf", "changeSetId": "cs-1"})
    assert r.status_code == 202  # 실패한 반영은 멱등 대상이 아니다
    starter.run_all()
    assert c.get(f"/v1/applies/{r.json()['applyId']}").json()["status"] == "applied"


def test_review_flow_and_rollback(tmp_path):
    c, starter, _ = build(tmp_path)
    c.post("/v1/apply", json={"targetId": "wf", "changeSetId": "cs-1"})
    starter.run_all()
    v1 = c.get("/v1/applies/ap-1").json()["toolVersion"]
    c.post("/v1/apply", json={"targetId": "wf", "changeSetId": "cs-2"})
    starter.run_all()
    pend = c.get("/v1/reviews", params={"targetId": "wf", "status": "pending"}).json()["reviews"]
    assert [p["reasons"] for p in pend] == [["TOOL_REMOVED"]]
    rid = pend[0]["reviewId"]
    assert c.get(f"/v1/reviews/{rid}").json()["operationId"] == "pages.get"
    d = c.post(f"/v1/reviews/{rid}/decision", json={"decision": "approve", "by": "운영자", "note": "확인"})
    assert d.status_code == 200 and d.json()["status"] == "approved"
    again = c.post(f"/v1/reviews/{rid}/decision", json={"decision": "reject"})
    assert again.status_code == 409 and again.json()["error"]["code"] == "REVIEW_ALREADY_DECIDED"
    r = c.post("/v1/apply", json={"targetId": "wf"})
    starter.run_all()
    v3 = c.get(f"/v1/applies/{r.json()['applyId']}").json()["toolVersion"]
    assert v3 != v1
    rb = c.post("/v1/rollback", json={"targetId": "wf", "toolVersion": v1})
    assert rb.status_code == 200 and rb.json()["currentToolVersion"] == v1
    st = c.get("/v1/targets/wf/state").json()
    assert st["currentToolVersion"] == v1 and st["headToolVersion"] == v3
    assert v1 in st["toolVersions"] and v3 in st["toolVersions"]
    assert c.post("/v1/rollback", json={"targetId": "wf", "toolVersion": "tv-000099-aaaaaaaa"}).status_code == 404


def test_unknown_review_and_apply(tmp_path):
    c, _, _ = build(tmp_path)
    assert c.get("/v1/reviews/rv-x").json()["error"]["code"] == "REVIEW_NOT_FOUND"
    assert c.get("/v1/applies/ap-x").json()["error"]["code"] == "APPLY_NOT_FOUND"


def test_health_and_openapi(tmp_path):
    c, _, _ = build(tmp_path)
    assert c.get("/health").json() == {"status": "ok", "toolStore": "ok"}
    assert "/v1/apply" in c.get("/openapi.json").json()["paths"]


def test_bearer_auth(tmp_path):
    c, _, _ = build(tmp_path, auth="bearer")
    assert c.post("/v1/apply", json={"targetId": "wf"}).status_code == 401
    assert c.post("/v1/apply", json={"targetId": "wf"}, headers={"Authorization": "Bearer tk"}).status_code == 202
    assert c.get("/health").status_code == 200
