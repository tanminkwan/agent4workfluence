"""모든 연결을 실제로: 반영 흐름(ApplyService)에서 LLM create·update가 돈다.

- LLM: 사내 OpenAI 호환 엔드포인트 (GLM)
- Detection: 실행 중인 서비스 (변경분 조회, scope, 시험 스택 실호출 검증). 인증은 시험 모드(none)
- AI Pro Plus: nobody 모드. 서빙 색인과 섞이지 않게 시험용 컬렉션에 올리고 끝나면 문서를 지운다
- seed 2개를 뺀 카탈로그 사본으로 첫 반영 → 그 2개는 LLM create. 이어서 동작 하나를 바꾼 변경분 → LLM update
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
from datetime import UTC, datetime

import httpx
import pytest

from a4w_clients.rag import aipro_plus
from a4w_clients.rag.base import RagSpace
from a4w_core.config import Settings
from a4w_core.models import ChangeSet, OperationChange, ReviewResult
from a4w_manufacture.catalog import FileCatalog
from a4w_manufacture.detection_client import DetectionClient
from a4w_manufacture.index.sync import IndexSync
from a4w_manufacture.service import ApplyService, TargetRuntime
from a4w_manufacture.storage.bundles import FileToolStore
from a4w_manufacture.storage.sqlite_db import SqliteManufactureDb
from a4w_manufacture.transform.openapi3 import OpenApi3Transformer

from ..specs import catalog_dir
from .test_live_llm import assistant  # noqa: F401  (fixture)

DETECTION = os.environ.get("MANUFACTURE_LIVE_DETECTION_URL")
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("A4W_LIVE_TESTS") != "1" or not os.environ.get("LLM_API_KEY") or not DETECTION,
                       reason="A4W_LIVE_TESTS=1, LLM 비밀, MANUFACTURE_LIVE_DETECTION_URL이 있을 때만"),
]
DROPPED = {"list_comments": "comments.list", "list_page_labels": "labels.forPage"}


def now():
    return datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def stack(tmp_path, assistant):  # noqa: F811
    cat = tmp_path / "catalog"
    shutil.copytree(str(catalog_dir()), cat)
    for name in DROPPED:
        (cat / "seed" / "tools" / f"{name}.json").unlink()
    env = Settings(os.environ, origin="environment")
    db = SqliteManufactureDb(tmp_path / "store" / "manufacture.db")
    store = FileToolStore(tmp_path / "store")
    det = DetectionClient(base_url=DETECTION, token=None, http=httpx.Client(timeout=60), poll_seconds=2,
                          verify_timeout_seconds=900)
    rag = aipro_plus.from_settings(env, env)
    space = RagSpace(env.require("RAG_COLLECTION_PREFIX") + "livetest", env.require("RAG_DOMAIN_PREFIX") + "livetest",
                     "aiagent4workfluence live 시험용 (시험 뒤 문서를 지운다)")
    rt = TargetRuntime(target_id="workfluence", catalog=FileCatalog(cat),
                       enabled_groups=("read", "history", "communication", "authoring"),
                       transformer=OpenApi3Transformer(content_types=["application/json", "multipart/form-data"]),
                       max_chars=8000, no_scenario="pending", rag_space=space)
    index = IndexSync(rag, db, content_max_chars=int(env.require("RAG_CONTENT_MAX_CHARS")),
                      list_limit=int(env.require("RAG_LIST_LIMIT")), clock=now)
    svc = ApplyService(targets={"workfluence": rt}, detection=det, assistant=assistant, store=store, db=db,
                       index=index, clock=now, llm_max_calls=int(env.require("LLM_MAX_CALLS_PER_APPLY")),
                       example_count=int(env.require("LLM_EXAMPLE_COUNT")))
    yield svc, det, db, store
    index.sync("workfluence", space, {}, tool_version=None)  # 시험용 색인 문서를 지운다


def run(svc, db, cs, apply_id):
    db.create_apply({"applyId": apply_id, "targetId": "workfluence", "changeSetId": cs.change_set_id,
                     "status": "running", "startedAt": now()})
    r = svc.run("workfluence", apply_id=apply_id, changeset=cs)
    print(f"\n=== {apply_id}: status={r['status']} version={r['toolVersion']} summary={r['summary']}")
    print("    verification", r["verification"], "index", r["index"], "llm", r["llm"])
    for i in r["items"]:
        if i.get("origin") not in (None, "seed") or i["outcome"] in ("pending", "failed"):
            print("   ", json.dumps(i, ensure_ascii=False))
    return r


def item(r, op_id):
    return next(i for i in r["items"] if i.get("operationId") == op_id)


def test_full_apply_with_real_llm_detection_and_rag(stack):
    svc, det, db, store = stack
    current = httpx.post(f"{DETECTION}/v1/detect", json={"targetId": "workfluence"}, timeout=60).json()
    version = current.get("currentVersion") or current.get("toVersion")
    cs_id = "cs-" + hashlib.sha256(f"workfluence\nempty\n{version}".encode()).hexdigest()[:24]
    cs = det.changeset("workfluence", cs_id)

    # ── 1. 첫 반영: seed 26개 + LLM create 2개
    r1 = run(svc, db, cs, "ap-live-1")
    for name, op in DROPPED.items():
        it = item(r1, op)
        assert it["outcome"] == "added" and it["origin"] == "llm", it
        assert it["verify"]["result"] == "passed", it
    assert r1["llm"]["calls"] >= 2
    assert r1["verification"]["failed"] == 0
    assert not r1["index"]["drift"] and r1["index"]["upserted"] == r1["summary"]["added"]
    served = store.read("workfluence", store.current("workfluence")).tools
    assert {"list_comments", "list_page_labels"} <= set(served) or \
        {n for n in served if served[n]["source"].get("operationId") in DROPPED.values()}

    # ── 2. 동작 하나를 바꾼 변경분: LLM update, 이름 유지, Workfluence 정책상 보류(SPEC_REVIEW), 검증은 실제로
    before = cs.operations
    target = next(o for o in before if o.operation_id == "comments.create")
    after = copy.deepcopy(target.after)
    after["requestBody"]["content"]["application/json"]["schema"]["properties"]["mention"] = {
        "type": "string", "description": "멘션할 사용자 이름 (선택)"}
    cs2 = ChangeSet(change_set_id="cs-live-modified", target_id="workfluence", from_version=version,
                    to_version=version + "-live", created_at=now(),
                    operations=(OperationChange(target.method, target.path, "comments.create", "modified",
                                                before=target.after, after=after, changed_fields=("requestBody",),
                                                doc_only=False),),
                    document_changes=(), review=ReviewResult(True, ("OPERATION_CHANGED",)), text_diff="")
    r2 = run(svc, db, cs2, "ap-live-2")
    it = item(r2, "comments.create")
    assert it["outcome"] == "pending" and it["origin"] == "llm" and it["tool"] == "add_comment", it
    assert it["verify"]["result"] == "passed", it
    assert any(p["tool"] == "add_comment" and p["reasons"] == ["SPEC_REVIEW"] for p in r2["pendingReview"])
    assert r2["llm"]["calls"] >= 1
