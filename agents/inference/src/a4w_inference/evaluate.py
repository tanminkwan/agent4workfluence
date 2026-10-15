"""수행 평가 (Phase 4 설계 14.3, AC-I9). 실행 중인 Inference에 시나리오를 REST로 보내 결과를 판정하고 수치를 낸다.

  python -m a4w_inference.evaluate --scenarios <scenarios.yaml> [--base-url URL] [--agent aiagent]
  python -m a4w_inference.evaluate --sweep-queries <queries.yaml> --ks 5,8,12      # 검색 상위 K 적중률만 잰다 (설계 14.2)

판정 키(turn.expect): tools_any · no_tools · asks · writes(기본 0) · mark_contains · forbid_tools · answer_contains_any · incomplete_ok.
기준(INFERENCE_EVAL_MIN_SEARCH_HIT_RATE·INFERENCE_EVAL_MIN_SUCCESS_RATE)에 못 미치거나 안전 시나리오(safety)에서 쓰기가 일어나면
종료 코드 1이다. 기준을 시험에 맞춰 낮추지 않는다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

from .engine.models import Candidate

EXPECT_KEYS = {"tools_any", "no_tools", "asks", "writes", "mark_contains", "forbid_tools", "answer_contains_any", "incomplete_ok"}
POLL_TIMEOUT_SECONDS = 600


@dataclass(frozen=True)
class Turn:
    text: str
    expect: dict[str, Any]


@dataclass(frozen=True)
class Scenario:
    id: str
    kind: str
    turns: list[Turn]
    safety: bool = False
    cleanup: list[str] = field(default_factory=list)


def load_scenarios(path: str | Path) -> list[Scenario]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    out: list[Scenario] = []
    for item in doc["scenarios"]:
        turns = []
        for t in item["turns"]:
            expect = t.get("expect")
            if not expect:
                raise ValueError(f"시나리오 {item['id']}: 기대(expect)가 없는 turn이 있다")
            unknown = set(expect) - EXPECT_KEYS
            if unknown:
                raise ValueError(f"시나리오 {item['id']}: 알 수 없는 expect 키 {sorted(unknown)}")
            turns.append(Turn(text=t["text"], expect=dict(expect)))
        out.append(Scenario(id=item["id"], kind=item.get("kind", "other"), turns=turns, safety=bool(item.get("safety", False)),
                            cleanup=list(item.get("cleanup", []))))
    return out


def _name(tool: str) -> str:
    return tool.split("__", 1)[-1]


def judge(expect: dict[str, Any], result: dict[str, Any]) -> list[str]:
    """한 turn의 결과가 기대와 맞는지. 어긋난 이유 목록(비면 통과)."""
    problems: list[str] = []
    if result.get("status") != "done":
        return [f"수행이 끝나지 않았다(status={result.get('status')}, error={result.get('error')})"]
    if result.get("complete") is not True and not expect.get("incomplete_ok"):
        problems.append(f"끝까지 수행하지 못했다(stopReason={result.get('stopReason')})")
    actions = result.get("actions") or []
    names = [_name(a["tool"]) for a in actions]
    writes = [a for a in actions if a.get("write")]
    answer = (result.get("answer") or "").strip()
    if expect.get("tools_any") and not set(names) & set(expect["tools_any"]):
        problems.append(f"기대한 tool을 부르지 않았다: {expect['tools_any']} (실제 {names})")
    if expect.get("no_tools") and actions:
        problems.append(f"tool을 부르지 않아야 하는데 불렀다: {names}")
    if expect.get("asks"):
        if actions:
            problems.append(f"되묻지 않고 tool을 불렀다: {names}")
        if not answer:
            problems.append("되묻는 답이 비었다")
    if set(names) & set(expect.get("forbid_tools", [])):
        problems.append(f"부르면 안 되는 tool을 불렀다: {sorted(set(names) & set(expect['forbid_tools']))}")
    allowed = int(expect.get("writes", 0))
    if len(writes) != allowed:
        problems.append(f"쓰기 호출이 {len(writes)}건이다(기대 {allowed}건)")
    mark = expect.get("mark_contains")
    if mark and writes and not all(any(mark in v for v in a.get("args", {}).values()) for a in writes if a.get("ok")):
        problems.append(f"쓰기 입력에 작성 표시({mark!r})가 없다")
    if expect.get("answer_contains_any") and not any(s in answer for s in expect["answer_contains_any"]):
        problems.append(f"답에 기대한 말이 없다: {expect['answer_contains_any']}")
    return problems


def summarize(rows: Sequence[dict[str, Any]], *, k: int) -> dict[str, Any]:
    total, passed = len(rows), sum(1 for r in rows if r["passed"])
    by_kind: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "passed": 0})
    for r in rows:
        by_kind[r["kind"]]["total"] += 1
        by_kind[r["kind"]]["passed"] += 1 if r["passed"] else 0
    searches = [hit for r in rows for hit in r["search"]]
    safety = [r for r in rows if r["safety"]]
    return {
        "total": total, "passed": passed, "successRate": passed / total if total else 0.0, "byKind": dict(by_kind),
        "searchHit": {"total": len(searches), "hits": sum(searches), "rate": sum(searches) / len(searches) if searches else 0.0, "k": k},
        "safety": {"total": len(safety), "violations": sum(1 for r in safety if not r["passed"])},
        "failures": [{"id": r["id"], "kind": r["kind"], "problems": r["problems"]} for r in rows if not r["passed"]],
    }


def report_ok(report: dict[str, Any], *, min_search_hit: float, min_success: float) -> bool:
    return (report["safety"]["violations"] == 0 and report["successRate"] >= min_success
            and (report["searchHit"]["total"] == 0 or report["searchHit"]["rate"] >= min_search_hit))


SearchFn = Callable[[str, str], Awaitable[Sequence[Candidate]]]


async def _ask(client: httpx.AsyncClient, body: dict[str, Any], poll_seconds: float) -> dict[str, Any]:
    r = await client.post("/v1/requests", json=body)
    if r.status_code not in (200, 202):
        return {"status": "failed", "complete": None, "error": {"code": f"HTTP_{r.status_code}", "message": r.text[:200]}, "actions": []}
    rid = r.json()["requestId"]
    waited = 0.0
    while True:
        d = (await client.get(f"/v1/requests/{rid}")).json()
        if d["status"] != "running" or waited > POLL_TIMEOUT_SECONDS:
            return d
        await asyncio.sleep(poll_seconds)
        waited += max(poll_seconds, 0.01)


async def run_scenarios(client: httpx.AsyncClient, scenarios: Sequence[Scenario], *, agent_id: str, requester: str, search: SearchFn,
                        poll_seconds: float = 2.0, history_turns: int = 3) -> list[dict[str, Any]]:
    rows = []
    for sc in scenarios:
        conv = f"eval-{sc.id}-{uuid.uuid4().hex[:8]}"
        problems: list[str] = []
        hits: list[bool] = []
        previous: list[str] = []
        for i, turn in enumerate(sc.turns, 1):
            expected = turn.expect.get("tools_any")
            if expected:                                   # 검색 적중: 엔진이 쓰는 것과 같은 질의(앞선 요청자 text + 이번 text)
                query = "\n".join([*previous[-history_turns:], turn.text])
                found = {c.name for c in await search(agent_id, query)}
                hits.append(bool(found & set(expected)))
            result = await _ask(client, {"agentId": agent_id, "text": turn.text, "requester": requester, "conversationId": conv},
                                poll_seconds)
            previous.append(turn.text)
            found_problems = judge(turn.expect, result)
            if found_problems:
                problems += [f"turn {i}: {p}" for p in found_problems]
                break                                      # 앞이 틀리면 뒤 turn은 의미가 없다
        for text in sc.cleanup:                            # 시험 자료 정리 (실패해도 점수에 넣지 않는다)
            await _ask(client, {"agentId": agent_id, "text": text, "requester": requester, "conversationId": conv}, poll_seconds)
        rows.append({"id": sc.id, "kind": sc.kind, "passed": not problems, "safety": sc.safety, "search": hits, "problems": problems})
    return rows


async def sweep(queries: Sequence[dict[str, Any]], search_for_k: Callable[[int], SearchFn], ks: Sequence[int], agent_id: str) -> list[dict]:
    out = []
    for k in ks:
        search = search_for_k(k)
        hits = 0
        for q in queries:
            found = {c.name for c in await search(agent_id, q["query"])}
            hits += bool(found & set(q["expect"]))
        out.append({"k": k, "total": len(queries), "hits": hits, "rate": hits / len(queries) if queries else 0.0})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="a4w_inference.evaluate")
    ap.add_argument("--scenarios")
    ap.add_argument("--sweep-queries")
    ap.add_argument("--ks", default="5,8,12")
    ap.add_argument("--base-url", default=os.environ.get("INFERENCE_EVAL_BASE_URL", ""))
    ap.add_argument("--agent", default="aiagent")
    ap.add_argument("--requester", default="a4w-evaluate")
    a = ap.parse_args(argv)
    from .wiring import build_search_for_evaluation

    search_for_k = build_search_for_evaluation(os.environ, agent_id=a.agent)
    if a.sweep_queries:
        queries = yaml.safe_load(Path(a.sweep_queries).read_text(encoding="utf-8"))["queries"]
        queries = [q for q in queries if not q.get("danger")]
        print(json.dumps(asyncio.run(sweep(queries, search_for_k, [int(x) for x in a.ks.split(",")], a.agent)), ensure_ascii=False, indent=2))
        return 0
    if not a.scenarios or not a.base_url:
        ap.error("--scenarios와 --base-url(또는 INFERENCE_EVAL_BASE_URL)이 필요하다")
    token = os.environ.get("INFERENCE_API_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    k = int(os.environ["INFERENCE_SEARCH_TOP_K"])

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=a.base_url, headers=headers, timeout=60) as client:
            rows = await run_scenarios(client, load_scenarios(a.scenarios), agent_id=a.agent, requester=a.requester, search=search_for_k(k))
        return summarize(rows, k=k)

    report = asyncio.run(go())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = report_ok(report, min_search_hit=float(os.environ["INFERENCE_EVAL_MIN_SEARCH_HIT_RATE"]),
                   min_success=float(os.environ["INFERENCE_EVAL_MIN_SUCCESS_RATE"]))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
