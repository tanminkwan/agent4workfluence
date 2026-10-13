"""색인 평가 (Phase 2 설계 14.5절, FR-296)."""

from __future__ import annotations

from a4w_clients.rag.base import RagHit, RagSpace, RagSpaceRef, RagStore
from a4w_manufacture.evaluate_index import evaluate

SPACE = RagSpace("a4w_tools_wf", "a4w-tools-wf", "d")


class RankedRag(RagStore):
    def __init__(self, answers):
        self.answers = answers

    def ensure_space(self, space):
        return RagSpaceRef(space.collection, 1)

    def upsert(self, ref, doc): ...

    def delete(self, ref, doc_id): ...

    def list_ids(self, ref, *, limit):
        return {}

    def search(self, ref, query, *, limit):
        names = self.answers.get(query, [])
        return [RagHit(id=n, score=1.0 - i / 10, content="", extended_content="", source=f"wf/{n}@r-1")
                for i, n in enumerate(names)][:limit]


def test_hit_rate_top_n_and_scores():
    rag = RankedRag({"q1": ["a", "b"], "q2": ["x", "y", "b"], "q3": ["z"], "q4": ["d"]})
    queries = [{"query": "q1", "expect": ["a"]}, {"query": "q2", "expect": ["b"]}, {"query": "q3", "expect": ["c"]},
               {"query": "q4", "expect": ["d"], "danger": True}]
    r = evaluate(rag, SPACE, "wf", queries, top_n=2, include_danger=False)
    assert r["total"] == 3 and r["hits"] == 1 and r["top1"] == 1
    assert round(r["hitRate"], 3) == round(1 / 3, 3)
    assert [i["rank"] for i in r["items"]] == [1, None, None]  # 순위는 상위 N 안에서만 잰다
    assert r["scores"]["hit"] == [1.0] and len(r["scores"]["miss"]) == 2
    assert evaluate(rag, SPACE, "wf", queries, top_n=3, include_danger=True)["total"] == 4


def test_any_expected_tool_counts():
    rag = RankedRag({"q": ["b", "a"]})
    r = evaluate(rag, SPACE, "wf", [{"query": "q", "expect": ["a", "c"]}], top_n=2, include_danger=False)
    assert r["hits"] == 1 and r["items"][0]["rank"] == 2
