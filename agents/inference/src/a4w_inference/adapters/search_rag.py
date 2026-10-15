"""ToolSearch 구현: AI Pro Plus(RagStore) 검색 결과를 후보 tool로 바꾼다 (Phase 4 설계 6.2).

색인은 Manufacture가 쓴다. 여기서는 읽기(검색)만 한다. 점수 컷은 설정(0 = 끔), 상위 K도 설정이다.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

import anyio

from a4w_clients.rag.base import RagError, RagSpace, RagSpaceRef, RagStore

from ..engine.models import Candidate, SearchUnavailable
from ..engine.ports import ToolSearch

log = logging.getLogger(__name__)


def parse_source(source: str, target_id: str) -> tuple[str, str] | None:
    """`<targetId>/<toolName>@<revision>` → (toolName, revision). 모양이 다르거나 다른 타겟이면 None."""
    prefix, sep, rest = source.partition("/")
    if not sep or prefix != target_id:
        return None
    name, at, revision = rest.partition("@")
    if not at or not name or not revision or "/" in name or "@" in revision:
        return None
    return name, revision


class RagToolSearch(ToolSearch):
    def __init__(self, rag: RagStore, *, spaces: Mapping[str, RagSpace], agent_targets: Mapping[str, Sequence[str]], top_k: int,
                 min_score: float):
        self._rag, self._spaces, self._agent_targets = rag, dict(spaces), {a: tuple(t) for a, t in agent_targets.items()}
        self._top_k, self._min_score = top_k, min_score
        self._refs: dict[str, RagSpaceRef] = {}

    async def search(self, agent_id: str, query: str) -> list[Candidate]:
        targets = self._agent_targets.get(agent_id)
        if targets is None:
            raise SearchUnavailable(f"검색 설정이 없는 agent다: {agent_id}")
        found: list[Candidate] = []
        for target in targets:
            try:
                hits = await anyio.to_thread.run_sync(self._search_one, target, query)
            except RagError:
                log.warning("tool search failed: target=%s", target)      # 질의는 남기지 않는다
                raise SearchUnavailable("tool 검색(AI Pro Plus)을 쓸 수 없다") from None
            for h in hits:
                parsed = parse_source(h.source, target)
                if parsed is None:
                    log.info("search hit dropped (unexpected source) target=%s", target)
                    continue
                if self._min_score > 0 and h.score < self._min_score:
                    continue
                found.append(Candidate(target_id=target, name=parsed[0], revision=parsed[1], score=h.score))
        return sorted(found, key=lambda c: -c.score)

    def _search_one(self, target: str, query: str):
        ref = self._refs.get(target)
        if ref is None:
            ref = self._refs[target] = self._rag.ensure_space(self._spaces[target])
        return self._rag.search(ref, query, limit=self._top_k)
