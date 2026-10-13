"""추가된 API 반영의 실호출 끝단 (AC-3b의 5·6). A4W_LIVE_TESTS=1, LLM 비밀, 실행 중인 Detection·AI Pro Plus가 있을 때만.

seed에서 뺀 실제 동작(comments.list)을 실제 LLM으로 만들고 → Detection 검증 API로 시험 스택에서 실제로 부르고 →
AI Pro Plus에 (시험용 source로) 올려 그 tool의 예시 질의로 검색한 뒤 지운다. 서빙 중인 색인은 건드리지 않는다.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

from a4w_clients.rag import aipro_plus
from a4w_clients.rag.base import RagSpace
from a4w_core.config import Settings
from a4w_manufacture.assembly import assemble, parse_authoring
from a4w_manufacture.detection_client import DetectionClient
from a4w_manufacture.fingerprint import fragment_sha, structure_sha
from a4w_manufacture.index.sync import index_document

from ..specs import fragment
from .test_live_llm import CATALOG, SELECTOR, T, assistant  # noqa: F401  (fixture)

DETECTION = os.environ.get("MANUFACTURE_LIVE_DETECTION_URL")
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("A4W_LIVE_TESTS") != "1" or not os.environ.get("LLM_API_KEY") or not DETECTION,
                       reason="A4W_LIVE_TESTS=1, LLM 비밀, MANUFACTURE_LIVE_DETECTION_URL이 있을 때만"),
]
TOP_N = int(os.environ.get("INDEX_EVAL_TOP_N", "5"))


def test_llm_made_tool_passes_real_call_and_is_found(assistant):  # noqa: F811
    frag = fragment("comments.list")
    sel = SELECTOR.select(frag)
    sk = T.skeleton(frag, fixed=SELECTOR.fixed_values(frag))
    taken = {t["name"] for t in CATALOG.seeds()} - {"list_comments"}
    out = assistant.author("create", sk, frag, previous=None, taken_names=taken,
                           examples=CATALOG.examples(group=sel.group, method="GET", count=3, exclude="list_comments"))
    source = {"operationId": "comments.list", "method": "GET", "path": frag["path"], "specVersion": "live",
              "fragmentSha256": fragment_sha(frag), "structureSha256": structure_sha(frag)}
    d = assemble(sk, parse_authoring(out.raw, max_chars=8000), group=sel.group, risk=sel.risk, origin="llm",
                 source=source)

    # (5) Detection 검증 API로 시험 스택에서 실제로 부른다 (시나리오 없음 → 자동 시나리오)
    det = DetectionClient(base_url=DETECTION, token=os.environ.get("DETECTION_CLIENT_TOKEN") or None,
                          http=httpx.Client(timeout=30), poll_seconds=2, verify_timeout_seconds=300)
    v = det.verify("workfluence", f"vf-live-added-{uuid.uuid4().hex[:8]}", tools=[d], references=[], scenarios={})
    print("\nverify:", v["results"][d["name"]], v["sandbox"])
    assert v["results"][d["name"]]["result"] == "passed"
    assert v["sandbox"]["cleanup"] == "ok"

    # (6) 색인 뒤 예시 질의로 검색하면 상위 N개 안에 든다. 서빙 색인과 섞이지 않게 시험용 target id로 올린다
    env = Settings(os.environ, origin="environment")
    rag = aipro_plus.from_settings(env, env)
    tid = "workfluence"
    space = RagSpace(env.require("RAG_COLLECTION_PREFIX") + tid, env.require("RAG_DOMAIN_PREFIX") + tid, "")
    ref = rag.ensure_space(space)
    doc = index_document("livetest", d, tool_version=None, max_chars=int(env.require("RAG_CONTENT_MAX_CHARS")))
    rag.upsert(ref, doc)
    try:
        ranks = []
        for q in d["index"]["examples"]:
            hits = rag.search(ref, q, limit=TOP_N)
            rank = next((i + 1 for i, h in enumerate(hits) if h.id == doc.id), None)
            ranks.append(rank)
            print(f"  {q!r}: rank={rank} top={[h.source.split('@')[0] for h in hits]}")
        assert all(r is not None for r in ranks)
    finally:
        rag.delete(ref, doc.id)
