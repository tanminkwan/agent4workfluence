"""RagToolSearch: AI Pro Plus 검색 결과를 후보로 바꾼다 (설계 6.2, 부록 B.1 A6·E9). TDD red."""

from __future__ import annotations

import pytest

from a4w_clients.rag.base import RagError, RagHit, RagSpace, RagSpaceRef, RagStore
from a4w_inference.adapters.search_rag import RagToolSearch
from a4w_inference.engine.models import Candidate, SearchUnavailable
from a4w_inference.engine.ports import ToolSearch

pytestmark = pytest.mark.anyio


def hit(source, score, id_="x"):
    return RagHit(id=id_, score=score, content="c", extended_content="e", source=source)


class FakeRag(RagStore):
    def __init__(self, hits_by_collection, fail=False):
        self.hits = hits_by_collection
        self.searches: list[tuple] = []
        self.ensured: list[str] = []
        self.fail = fail

    def ensure_space(self, space: RagSpace) -> RagSpaceRef:
        self.ensured.append(space.collection)
        return RagSpaceRef(collection=space.collection, domain_id=7)

    def search(self, ref, query, *, limit):
        self.searches.append((ref.collection, query, limit))
        if self.fail:
            raise RagError("down")
        return list(self.hits.get(ref.collection, []))

    def upsert(self, ref, doc): raise AssertionError("검색 client는 쓰지 않는다")
    def delete(self, ref, doc_id): raise AssertionError("검색 client는 쓰지 않는다")
    def list_ids(self, ref, *, limit): raise AssertionError("검색 client는 쓰지 않는다")


def build(rag, *, top_k=8, min_score=0.0, targets=("workfluence",)):
    spaces = {t: RagSpace(collection=f"a4w_tools_{t}", domain=f"a4w-tools-{t}", description="d") for t in targets}
    return RagToolSearch(rag, spaces=spaces, agent_targets={"aiagent": tuple(targets)}, top_k=top_k, min_score=min_score)


async def test_hits_become_candidates_with_target_name_and_revision_from_source():
    rag = FakeRag({"a4w_tools_workfluence": [hit("workfluence/search_pages@r-3f2a", 0.91), hit("workfluence/read_page@r-77", 0.84)]})
    s = build(rag)
    assert isinstance(s, ToolSearch)
    got = await s.search("aiagent", "회의록 찾아줘")
    assert got == [Candidate("workfluence", "search_pages", "r-3f2a", 0.91), Candidate("workfluence", "read_page", "r-77", 0.84)]
    assert rag.searches == [("a4w_tools_workfluence", "회의록 찾아줘", 8)]


async def test_top_k_is_passed_to_the_search_and_a_space_is_resolved_once():
    rag = FakeRag({})
    s = build(rag, top_k=5)
    await s.search("aiagent", "a")
    await s.search("aiagent", "b")
    assert [x[2] for x in rag.searches] == [5, 5] and rag.ensured == ["a4w_tools_workfluence"]


@pytest.mark.parametrize("source", ["", "garbage", "workfluence/search_pages", "workfluence/@r-1", "/x@r-1", "other/search_pages@r-1",
                                    "workfluence/a/b@r-1"])
async def test_malformed_or_foreign_sources_are_dropped(source):
    rag = FakeRag({"a4w_tools_workfluence": [hit(source, 0.9), hit("workfluence/read_page@r-1", 0.8)]})
    got = await build(rag).search("aiagent", "x")
    assert [c.name for c in got] == ["read_page"]


async def test_min_score_zero_means_no_cut_and_positive_cuts_below():
    hits = [hit("workfluence/a@r-1", 0.82), hit("workfluence/b@r-1", 0.60)]
    assert len(await build(FakeRag({"a4w_tools_workfluence": hits}), min_score=0.0).search("aiagent", "x")) == 2
    got = await build(FakeRag({"a4w_tools_workfluence": hits}), min_score=0.7).search("aiagent", "x")
    assert [c.name for c in got] == ["a"]


async def test_results_from_several_targets_are_merged_by_score():
    rag = FakeRag({"a4w_tools_workfluence": [hit("workfluence/a@r-1", 0.7)], "a4w_tools_other": [hit("other/b@r-2", 0.9)]})
    got = await build(rag, targets=("workfluence", "other")).search("aiagent", "x")
    assert [(c.target_id, c.name) for c in got] == [("other", "b"), ("workfluence", "a")]


async def test_rag_failure_is_search_unavailable():
    with pytest.raises(SearchUnavailable):
        await build(FakeRag({}, fail=True)).search("aiagent", "x")


async def test_unknown_agent_is_search_unavailable_not_a_crash():
    with pytest.raises(SearchUnavailable):
        await build(FakeRag({})).search("ghost", "x")


async def test_a_failure_does_not_leak_the_query_or_hit_bodies():
    rag = FakeRag({}, fail=True)
    with pytest.raises(SearchUnavailable) as e:
        await build(rag).search("aiagent", "비밀 질의")
    assert "비밀 질의" not in str(e.value)
