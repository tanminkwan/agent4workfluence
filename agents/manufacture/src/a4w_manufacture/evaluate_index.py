"""색인 평가 (Phase 2 설계 14.5절, FR-296): 사람이 쓴 질의로 검색해 기대 tool이 상위 N개 안에 드는 비율을 잰다.

반영 흐름에는 넣지 않는다(질의 수십 건 검색). 명령으로 돌린다:
  python -m a4w_manufacture.evaluate_index --target workfluence [--include-danger]
  (설정은 manufacture와 같은 환경변수. 결과 JSON을 내고, 기준(INDEX_EVAL_MIN_HIT_RATE) 미만이면 종료 코드 1)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable, Mapping
from typing import Any

from a4w_clients.rag.base import RagSpace, RagStore


def _tool_of(source: str, target_id: str) -> str | None:
    prefix = f"{target_id}/"
    if not source.startswith(prefix):
        return None
    return source[len(prefix):].split("@", 1)[0]


def evaluate(rag: RagStore, space: RagSpace, target_id: str, queries: Iterable[Mapping[str, Any]], *, top_n: int,
             include_danger: bool) -> dict[str, Any]:
    ref = rag.ensure_space(space)
    items, hit_scores, miss_scores = [], [], []
    hits = top1 = 0
    for q in queries:
        if q.get("danger") and not include_danger:
            continue
        found = rag.search(ref, q["query"], limit=top_n)
        names = [_tool_of(h.source, target_id) for h in found]
        rank = next((i + 1 for i, n in enumerate(names) if n in q["expect"]), None)
        if rank is not None:
            hits += 1
            top1 += rank == 1
            hit_scores.append(found[rank - 1].score)
        elif found:
            miss_scores.append(found[0].score)
        else:
            miss_scores.append(0.0)
        items.append({"query": q["query"], "expect": list(q["expect"]), "rank": rank,
                      "top": [{"tool": n, "score": round(h.score, 4)} for n, h in zip(names, found)]})
    total = len(items)
    return {"total": total, "hits": hits, "top1": top1, "topN": top_n,
            "hitRate": hits / total if total else 0.0, "top1Rate": top1 / total if total else 0.0,
            "scores": {"hit": hit_scores, "miss": miss_scores}, "items": items}


def main(argv: list[str] | None = None) -> int:
    from .wiring import build_context

    ap = argparse.ArgumentParser(prog="evaluate_index")
    ap.add_argument("--target", required=True)
    ap.add_argument("--include-danger", action="store_true")
    a = ap.parse_args(argv)
    env = os.environ
    ctx = build_context(env)
    rt = ctx.service.targets[a.target]
    report = evaluate(ctx.service._index._rag, rt.rag_space, a.target, rt.catalog.queries(),
                      top_n=int(env["INDEX_EVAL_TOP_N"]), include_danger=a.include_danger)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["hitRate"] >= float(env["INDEX_EVAL_MIN_HIT_RATE"]) else 1


if __name__ == "__main__":
    sys.exit(main())
