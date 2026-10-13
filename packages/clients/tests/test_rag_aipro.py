"""AI Pro Plus RagStore (Phase 2 설계 14.2절). 서버 대신 가짜 HTTP로 경로·본문·앞머리 규약을 확인한다."""

from __future__ import annotations

import json

import httpx
import pytest

from a4w_clients.rag.aipro_plus import AiProPlusStore
from a4w_clients.rag.base import RagDocument, RagError, RagSpace


class FakeAiPro:
    def __init__(self):
        self.collections = [{"collection_name": "someone_else"}]
        self.domains = [{"name": "other", "id": 2}]
        self.points: dict[str, dict] = {}
        self.calls: list[tuple[str, str, dict | None]] = []
        self.fail_upsert = False

    def __call__(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        self.calls.append((req.method, req.url.path, body))
        assert "authorization" not in req.headers
        assert req.headers["X-Virtual-User"] == "a4w"
        p = req.url.path
        if (req.method, p) == ("GET", "/api/collections"):
            return httpx.Response(200, json=self.collections)
        if (req.method, p) == ("POST", "/api/collections"):
            self.collections.append({"collection_name": body["collection_name"]})
            return httpx.Response(200, json={"status": "success"})
        if (req.method, p) == ("GET", "/api/domains"):
            return httpx.Response(200, json=self.domains)
        if (req.method, p) == ("POST", "/api/domains"):
            self.domains.append({"name": body["name"], "id": 7})
            return httpx.Response(200, json={"id": 7, "name": body["name"]})
        if (req.method, p) == ("POST", "/api/rag/knowledge"):
            if self.fail_upsert:
                return httpx.Response(500, json={"detail": "boom"})
            self.points[body["point_id"]] = body
            return httpx.Response(200, json={"id": body["point_id"], "status": "success"})
        if req.method == "DELETE" and p.startswith("/api/rag/knowledge/"):
            self.points.pop(p.rsplit("/", 1)[1], None)
            return httpx.Response(200, json={"message": "success"})
        if (req.method, p) == ("POST", "/api/rag/search"):
            hits = [{"id": k, "score": 0.9 if body.get("query") else 0.0, "content": v["content"],
                     "extended_content": v["extended_content"], "source": v["source"], "collection": v["collection_name"],
                     "domain_id": v["domain_id"], "created_at": "t"} for k, v in self.points.items()]
            return httpx.Response(200, json=hits[: body["limit"]])
        return httpx.Response(404, json={"detail": "Not Found"})


def store(fake, **kw):
    http = httpx.Client(transport=httpx.MockTransport(fake))
    args = dict(base_url="http://aipro.test", auth_mode="none", api_key=None, virtual_user="a4w",
                collection_prefix="a4w_tools_", domain_prefix="a4w-tools-", max_attempts=2, http=http)
    args.update(kw)
    return AiProPlusStore(**args)


SPACE = RagSpace(collection="a4w_tools_workfluence", domain="a4w-tools-workfluence", description="tools")


def test_ensure_space_creates_once():
    fake = FakeAiPro()
    s = store(fake)
    ref = s.ensure_space(SPACE)
    assert ref.domain_id == 7
    ref2 = s.ensure_space(SPACE)
    assert ref2 == ref
    creates = [c for c in fake.calls if c[0] == "POST" and c[1] in ("/api/collections", "/api/domains")]
    assert len(creates) == 2


def test_existing_domain_is_reused():
    fake = FakeAiPro()
    fake.domains.append({"name": "a4w-tools-workfluence", "id": 5})
    fake.collections.append({"collection_name": "a4w_tools_workfluence"})
    assert store(fake).ensure_space(SPACE).domain_id == 5
    assert not [c for c in fake.calls if c[0] == "POST"]


def test_prefix_rule_protects_other_collections():
    with pytest.raises(RagError):
        store(FakeAiPro()).ensure_space(RagSpace(collection="someone_else", domain="a4w-tools-x", description=""))
    with pytest.raises(RagError):
        store(FakeAiPro()).ensure_space(RagSpace(collection="a4w_tools_x", domain="other", description=""))


def test_upsert_delete_list_search():
    fake = FakeAiPro()
    s = store(fake)
    ref = s.ensure_space(SPACE)
    doc = RagDocument(id="11111111-1111-5111-8111-111111111111", content="댓글 달기", extended_content='{"a":1}',
                      source="workfluence/add_comment@r-1")
    s.upsert(ref, doc)
    body = fake.points[doc.id]
    assert body == {"collection_name": "a4w_tools_workfluence", "domain_id": 7, "content": "댓글 달기",
                    "extended_content": '{"a":1}', "source": "workfluence/add_comment@r-1", "point_id": doc.id}
    assert s.list_ids(ref, limit=100) == {doc.id: "workfluence/add_comment@r-1"}
    hits = s.search(ref, "댓글", limit=5)
    assert hits[0].id == doc.id and hits[0].score == 0.9 and hits[0].source.endswith("@r-1")
    search_body = [c[2] for c in fake.calls if c[1] == "/api/rag/search"][-1]
    assert search_body["collection_id"] == "a4w_tools_workfluence" and search_body["search_method"] == "vector"
    s.delete(ref, doc.id)
    assert fake.points == {}


def test_server_error_is_rag_error_after_retries():
    fake = FakeAiPro()
    s = store(fake)
    ref = s.ensure_space(SPACE)
    fake.fail_upsert = True
    with pytest.raises(RagError):
        s.upsert(ref, RagDocument(id="1", content="c", extended_content="e", source="s"))
    assert sum(1 for c in fake.calls if c[1] == "/api/rag/knowledge") == 2


def test_bearer_mode_sends_key():
    seen = []

    def h(req):
        seen.append(req.headers.get("authorization"))
        return httpx.Response(200, json=[])

    s = AiProPlusStore(base_url="http://x", auth_mode="bearer", api_key="k", virtual_user=None,
                       collection_prefix="a4w_tools_", domain_prefix="a4w-tools-", max_attempts=1,
                       http=httpx.Client(transport=httpx.MockTransport(h)))
    s.collections()
    assert seen == ["Bearer k"]
