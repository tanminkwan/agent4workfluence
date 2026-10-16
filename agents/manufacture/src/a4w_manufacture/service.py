"""변경 반영 흐름 (Phase 2 설계 4.3절). interface에만 의존한다.

한 번의 반영은 "바라는 tool 묶음"을 다시 계산한다:
  동작 상태(변경분을 쌓은 것) → 대상 선정 → (그대로 · seed · LLM · 승인된 보류) → 검사 → 사람 확인 판단
  → Detection 실호출 검증 → 새 묶음 → (바뀌었으면) 새 tool 버전 → DB 커밋 → 포인터 교체 → 색인 맞추기.
동작 하나의 실패는 그 동작만 빠지고, 반영 전체 실패(Detection·저장소·LLM 인증)는 예외로 끝나며 아무것도 바꾸지 않는다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from a4w_clients.rag.base import RagSpace
from a4w_core.errors import TargetNotFound
from a4w_core.models import ChangeSet, OperationChange
from a4w_tools.validate import check_references, validate_tool

from .assembly import assemble, extract_authoring, parse_authoring, revision_of
from .assist.assistant import AssistFailed, LlmBudget
from .catalog import FileCatalog
from .checks import DefinitionChecker
from .errors import ToolVersionNotFound
from .fingerprint import fragment_sha, structure_sha
from .index.sync import IndexSync
from .ports import DetectionPort
from .selection import RISK_ORDER, Selector
from .storage.bundles import FileToolStore
from .storage.sqlite_db import OperationRecord, SqliteManufactureDb, TargetState
from .transform.base import SpecTransformer

log = logging.getLogger(__name__)

STATE_OF = {"added": "active", "updated": "active", "unchanged": "active", "pending": "pending", "failed": "failed",
            "excluded": "excluded", "codeOwned": "codeOwned", "removed": "removed", "rejected": "rejected"}
REPORTED = {"added", "updated", "removed", "pending", "failed"}


@dataclass(frozen=True)
class TargetRuntime:
    target_id: str
    catalog: FileCatalog
    enabled_groups: tuple[str, ...]
    transformer: SpecTransformer
    max_chars: int
    no_scenario: str                 # pending | exclude
    rag_space: RagSpace


@dataclass
class Plan:
    """동작(또는 상위 tool) 하나의 이번 반영 결과."""

    key: tuple[str, str] | None      # (METHOD, path), 상위 tool이면 None
    rec: OperationRecord | None
    outcome: str = "unchanged"
    tool_name: str | None = None
    current: dict[str, Any] | None = None       # head에 있던 정의
    definition: dict[str, Any] | None = None    # 이번에 만든 정의
    origin: str | None = None
    reason: str | None = None
    reasons: list[str] = field(default_factory=list)
    needs_verify: bool = False
    verify: dict[str, Any] | None = None
    error: Any = None
    approved: dict[str, Any] | None = None      # 이번에 적용하는 승인 보류
    keep_review: dict[str, Any] | None = None   # 그대로 둔 보류
    touched: OperationChange | None = None
    kind: str = "operation"

    def item(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.rec is not None:
            out.update(operationId=self.rec.operation_id, method=self.rec.method, path=self.rec.path)
        else:
            out["kind"] = self.kind
        if self.tool_name:
            out["tool"] = self.tool_name
        out["outcome"] = self.outcome
        if self.origin:
            out["origin"] = self.origin
        if self.reason:
            out["reason"] = self.reason
        if self.verify:
            out["verify"] = {k: v for k, v in self.verify.items() if k in ("result", "calls", "reason")}
        if self.error:
            out["error"] = self.error
        return out


class ApplyService:
    def __init__(
        self,
        *,
        targets: Mapping[str, TargetRuntime],
        detection: DetectionPort,
        assistant,
        checker: DefinitionChecker | None = None,
        store: FileToolStore,
        db: SqliteManufactureDb,
        index: IndexSync,
        clock: Callable[[], str],
        llm_max_calls: int,
        example_count: int,
    ):
        from .checks import TextLimits

        self.targets = dict(targets)
        self._detection = detection
        self._assistant = assistant
        self._checker = checker or DefinitionChecker(TextLimits(600, 120, 60), max_visible_inputs=4)
        self._store = store
        self._db = db
        self._index = index
        self._clock = clock
        self._llm_max = llm_max_calls
        self._examples = example_count

    # ──────────────────────────────────────────────────────────── 반영
    def run(self, target_id: str, *, apply_id: str, changeset: ChangeSet | None) -> dict[str, Any]:
        rt = self._runtime(target_id)
        state = self._db.state(target_id)
        head_version = state.head_tool_version if state else None
        head = self._store.read(target_id, head_version) if head_version else None
        head_tools = dict(head.tools) if head else {}
        head_verified = {e["name"]: e["verified"] for e in head.manifest["tools"]} if head else {}

        ops = self._db.operations(target_id)
        touched: dict[tuple[str, str], OperationChange] = {}
        spec_version = state.last_applied_spec_version if state else None
        if changeset is not None:
            spec_version = changeset.to_version
            for oc in changeset.operations:
                touched[(oc.method, oc.path)] = oc
                ops[(oc.method, oc.path)] = _record(target_id, oc, ops.get((oc.method, oc.path)), changeset.to_version,
                                                    self._clock())

        selector = Selector(rt.catalog.rules(), enabled_groups=rt.enabled_groups,
                            token_scopes=self._detection.scopes(target_id))
        reviews = self._db.reviews(target_id)
        by_status = {s: {(r["method"], r["path"]): r for r in reviews if r["status"] == s and r["kind"] == "tool"}
                     for s in ("approved", "rejected", "pending")}
        assistant = self._assistant.with_budget(LlmBudget(self._llm_max))
        assigned: set[str] = set()

        plans = [
            self._plan(rt, key, ops[key], selector, head_tools, by_status, touched, changeset, assistant, assigned)
            for key in sorted(ops, key=lambda k: (k[1], k[0]))
        ]
        op_final = self._operation_tools(plans)
        comp_plans = self._composites(rt, op_final, head_tools)

        self._verify(rt, apply_id, plans + comp_plans, op_final)
        op_final = self._operation_tools(plans)
        for cp in comp_plans:  # 검증 뒤 참조 tool이 빠졌으면 상위 tool도 빠진다
            if cp.outcome in ("added", "updated") and any(s["tool"] not in op_final for s in cp.definition["steps"]):
                cp.outcome, cp.reason = ("excluded", "DEPENDENCY_MISSING")

        new_tools = {**op_final, **self._composite_tools(comp_plans)}
        verified = {n: head_verified.get(n, "passed") for n in new_tools}
        for p in plans + comp_plans:
            if p.outcome in ("added", "updated") and p.verify and p.verify.get("result") == "unverified":
                verified[p.tool_name] = "unverified"

        changed = {n: t["revision"] for n, t in new_tools.items()} != {n: t["revision"] for n, t in head_tools.items()}
        version = head_version
        if changed:
            version = self._store.write(target_id, apply_id=apply_id, tools=new_tools, parent=head_version, meta={
                "specVersion": spec_version or "", "changeSetId": changeset.change_set_id if changeset else None,
                "createdAt": self._clock(), "verified": verified,
            })

        items = [p.item() for p in plans + comp_plans
                 if p.outcome in REPORTED or p.reason == "SEED_INVALID" or (p.key is not None and p.key in touched)]
        new_reviews = self._commit(target_id, apply_id, plans + comp_plans, ops, state, spec_version, changeset,
                                   version, items)
        if version and self._store.current(target_id) != version:
            self._store.set_current(target_id, version, updated_at=self._clock())
        index = self._index.sync(target_id, rt.rag_space, new_tools if version else {}, tool_version=version)

        pending = self._db.reviews(target_id, status="pending")
        status = "applied"
        if any(p.outcome in ("pending", "failed") for p in plans + comp_plans):
            status = "partial"
        elif not changed and not new_reviews:
            status = "noop"
        result = {
            "applyId": apply_id, "status": status, "targetId": target_id,
            "changeSetId": changeset.change_set_id if changeset else None,
            "fromVersion": changeset.from_version if changeset else None,
            "toVersion": changeset.to_version if changeset else spec_version,
            "toolVersion": version,
            "summary": _summary(items),
            "items": items,
            "pendingReview": [{"reviewId": r["reviewId"], "operationId": r["operationId"], "tool": r["toolName"],
                               "reasons": r["reasons"]} for r in pending],
            "excluded": [{"operationId": i.get("operationId"), "reason": i.get("reason")} for i in items
                         if i["outcome"] == "excluded"],
            "codeOwnedChanges": [{"operationId": oc.operation_id, "method": oc.method, "path": oc.path,
                                  "change": oc.change} for key, oc in touched.items()
                                 if any(p.key == key and p.outcome == "codeOwned" for p in plans)],
            "verification": _verification_summary(plans + comp_plans),
            "index": index,
            "llm": {"calls": assistant.budget.used if hasattr(assistant, "budget") else 0},
        }
        log.info("apply done: target=%s apply=%s status=%s version=%s", target_id, apply_id, status, version)
        return result

    def _runtime(self, target_id: str) -> TargetRuntime:
        rt = self.targets.get(target_id)
        if rt is None:
            raise TargetNotFound(f"등록되지 않은 타겟: {target_id}")
        return rt

    # ──────────────────────────────────────────────────────────── 동작 하나
    def _seed_refresh(self, rt, rec, selector, sel, cur, head_tools, assigned, p: Plan) -> bool:
        """명세 조각이 그대로인 서빙 tool에 대해, catalog의 seed가 같은 조각인데 글이 바뀌었으면 True (설계 24.2).

        비교는 source(명세 판)를 뺀 정의로 한다. 바뀐 seed가 검사에 걸리면 서빙 tool을 그대로 두고 이유를 남긴다.
        """
        seed = rt.catalog.seed_for(rec.operation_id)
        if seed is None or rt.catalog.seed_match(rec.operation_id, rec.fragment_sha256, rec.structure_sha256) != "same":
            return False
        sk = rt.transformer.skeleton(rec.fragment, fixed=selector.fixed_values(rec.fragment))
        d = assemble(sk, extract_authoring(seed, sk), group=sel.group, risk=sel.risk, origin="seed", source=cur["source"])
        if d["revision"] == cur["revision"]:
            return False
        taken = (set(head_tools) | assigned) - {cur["name"]}
        problems = self._checker.check_seed(sk, seed, taken_names=taken)
        if problems:
            p.reason, p.error = "SEED_INVALID", [x.to_dict() for x in problems]
            log.warning("changed seed is invalid, served tool kept: op=%s", rec.operation_id)
            return False
        return True

    def _plan(self, rt, key, rec, selector, head_tools, by_status, touched, changeset, assistant, assigned) -> Plan:
        cur = head_tools.get(rec.tool_name) if rec.tool_name else None
        p = Plan(key=key, rec=rec, current=cur, tool_name=rec.tool_name if cur else None, touched=touched.get(key))
        approved, rejected, pending = (by_status[s].get(key) for s in ("approved", "rejected", "pending"))

        def removal(reason_note: str | None) -> Plan:
            if cur is None:
                p.outcome = "removed" if rec.fragment is None else "excluded"
                return p
            if approved is not None and approved.get("definition") is None:
                p.outcome, p.approved = "removed", approved
            elif rejected is not None and rejected.get("definition") is None:
                p.outcome = "rejected"
            elif pending is not None and pending.get("definition") is None:
                p.outcome, p.keep_review = "pending", pending
            else:
                p.outcome, p.reasons = "pending", ["TOOL_REMOVED"]
                if reason_note:
                    p.reason = reason_note
            return p

        if rec.fragment is None:
            return removal(None)
        sel = selector.select(rec.fragment)
        if sel.kind == "codeOwned":
            p.outcome = "codeOwned"
            return p
        if sel.kind == "excluded":
            if cur is not None:
                return removal(sel.reason)
            p.outcome, p.reason = "excluded", sel.reason
            return p

        fsha = rec.fragment_sha256
        if approved is not None and approved.get("definition") and approved.get("fragmentSha256") == fsha:
            d = approved["definition"]
            p.definition, p.tool_name, p.approved = d, d["name"], approved
            p.outcome, p.origin, p.verify = ("updated" if cur else "added"), d["origin"], approved.get("verify")
            assigned.add(d["name"])
            return p
        from_seed = False
        if cur is not None and cur["source"]["fragmentSha256"] == fsha and rec.state != "failed":
            if not self._seed_refresh(rt, rec, selector, sel, cur, head_tools, assigned, p):
                p.outcome = "unchanged"
                return p
            from_seed = True                       # seed 글만 바뀌었다: seed로 다시 만든다 (설계 24.2)
        if rejected is not None and rejected.get("fragmentSha256") == fsha:
            p.outcome = "rejected"
            return p
        if (pending is not None and pending.get("fragmentSha256") == fsha
                and pending.get("reasons") != ["UNVERIFIED"]):
            p.outcome, p.keep_review = "pending", pending
            return p
        # 검증 안 됨만으로 보류된 항목은 다시 만들고 다시 검증한다 (시나리오가 생겼을 수 있다). 이전 보류는 superseded

        # ── 새로 만들기
        frag = rec.fragment
        sk = rt.transformer.skeleton(frag, fixed=selector.fixed_values(frag))
        source = {"operationId": rec.operation_id, "method": rec.method, "path": rec.path,
                  "specVersion": rec.spec_version, "fragmentSha256": fsha, "structureSha256": rec.structure_sha256}
        taken = (set(head_tools) | assigned) - ({cur["name"]} if cur else set())
        task, previous = None, cur
        seed_same = rt.catalog.seed_match(rec.operation_id, fsha, rec.structure_sha256) == "same"
        if cur is not None and not from_seed and not seed_same:   # 새 명세에 맞는 seed가 있으면 seed로 (설계 25.1)
            task = "redescribe" if cur["source"]["structureSha256"] == rec.structure_sha256 else "update"
        else:
            match = rt.catalog.seed_match(rec.operation_id, fsha, rec.structure_sha256)
            seed = rt.catalog.seed_for(rec.operation_id)
            if match == "same":
                problems = self._checker.check_seed(sk, seed, taken_names=taken)
                if problems:
                    p.outcome, p.reason = "failed", "SEED_INVALID"
                    p.error = [x.to_dict() for x in problems]
                    return p
                p.definition = assemble(sk, extract_authoring(seed, sk), group=sel.group, risk=sel.risk,
                                        origin="seed", source=source)
                p.origin = "seed"
            else:
                task = {"doc_only": "redescribe", "changed": "update", "none": "create"}[match]
                previous = seed
        if task is not None:
            try:
                authored = assistant.author(
                    task, sk, frag, previous=previous, taken_names=taken,
                    examples=rt.catalog.examples(group=sel.group, method=rec.method, count=self._examples,
                                                 exclude=(previous or {}).get("name")),
                )
            except AssistFailed as e:
                p.outcome, p.reason, p.error = "failed", e.code, e.problems or None
                return p
            origin = "llm-doc" if task == "redescribe" else "llm"
            p.definition = assemble(sk, parse_authoring(authored.raw, max_chars=rt.max_chars), group=sel.group,
                                    risk=sel.risk, origin=origin, source=source)
            p.origin = "cache" if authored.origin == "cache" else origin
        problems = validate_tool(p.definition)
        if problems:
            p.outcome, p.reason, p.error, p.definition = "failed", "SCHEMA_INVALID", problems[:5], None
            return p

        p.tool_name = p.definition["name"]
        assigned.add(p.tool_name)
        if sel.risk == "danger" and (cur is None or cur["risk"] != "danger"):
            p.reasons.append("DANGER_ADDED")
        if cur is not None and sorted(cur["scopes"]) != sorted(p.definition["scopes"]):
            p.reasons.append("SCOPE_CHANGED")
        oc = touched.get(key)
        if (oc is not None and oc.change == "modified" and not oc.doc_only and changeset is not None
                and changeset.review.needs_review):
            p.reasons.append("SPEC_REVIEW")
        p.outcome = "pending" if p.reasons else ("updated" if cur else "added")
        if task == "redescribe" and cur is not None and cur["http"] == p.definition["http"]:
            p.verify = {"result": "inherited"}  # HTTP가 같으니 다시 부르지 않는다
        else:
            p.needs_verify = True
        return p

    @staticmethod
    def _operation_tools(plans: list[Plan]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for p in plans:
            if p.kind != "operation":
                continue
            if p.outcome in ("added", "updated") and p.definition:
                out[p.definition["name"]] = p.definition
            elif p.outcome != "removed" and p.current is not None:
                out[p.current["name"]] = p.current
        return out

    # ──────────────────────────────────────────────────────────── 상위 tool
    def _composites(self, rt: TargetRuntime, op_tools, head_tools) -> list[Plan]:
        plans = []
        for seed in rt.catalog.composites():
            name = seed["name"]
            cur = head_tools.get(name)
            p = Plan(key=None, rec=None, kind="composite", tool_name=name, current=cur)
            steps = [s["tool"] for s in seed["steps"]]
            if any(s not in op_tools for s in steps):
                p.outcome, p.reason = ("removed", "DEPENDENCY_REMOVED") if cur else ("excluded", "DEPENDENCY_MISSING")
                if cur is None:
                    p.tool_name = None
                plans.append(p)
                continue
            d = dict(seed)
            d["risk"] = max((op_tools[s]["risk"] for s in steps), key=RISK_ORDER.__getitem__)
            d["scopes"] = sorted({sc for s in steps for sc in op_tools[s]["scopes"]})
            d["origin"] = "seed"
            d["revision"] = revision_of(d)
            problems = validate_tool(d) + check_references({**op_tools, name: d})
            if problems:
                p.outcome, p.reason, p.error = "failed", "COMPOSITE_INVALID", problems[:5]
                plans.append(p)
                continue
            p.origin = "seed"
            if cur is not None and cur["revision"] == d["revision"]:
                p.outcome = "unchanged"
            else:
                p.definition = d
                p.needs_verify = True
                if d["risk"] == "danger" and (cur is None or cur["risk"] != "danger"):
                    p.reasons.append("DANGER_ADDED")
                p.outcome = "pending" if p.reasons else ("updated" if cur else "added")
            plans.append(p)
        return plans

    @staticmethod
    def _composite_tools(plans: list[Plan]) -> dict[str, dict[str, Any]]:
        out = {}
        for p in plans:
            if p.kind != "composite":
                continue
            if p.outcome in ("added", "updated") and p.definition:
                out[p.tool_name] = p.definition
            elif p.outcome not in ("removed", "excluded") and p.current is not None:
                out[p.tool_name] = p.current
        return out

    # ──────────────────────────────────────────────────────────── 검증 (Detection)
    def _verify(self, rt: TargetRuntime, apply_id: str, plans: list[Plan], op_tools) -> None:
        cands = [p for p in plans if p.needs_verify and p.definition is not None]
        if not cands:
            return
        names = {p.definition["name"] for p in cands}
        refs = {}
        for p in cands:
            if p.definition["kind"] == "composite":
                for s in p.definition["steps"]:
                    if s["tool"] not in names and s["tool"] in op_tools:
                        refs[s["tool"]] = op_tools[s["tool"]]
        scenarios = {}
        for n in sorted(names):
            sc = rt.catalog.scenario(n)
            if sc is None:
                continue
            setup_tools = [s["tool"] for s in sc.get("setup", [])]
            if any(t not in names and t not in op_tools for t in setup_tools):
                continue  # setup tool이 이번 묶음에 없다 → 시나리오 없이(검증 안 됨) 보낸다. 요청 전체가 거절되지 않게
            for t in setup_tools:
                if t not in names:
                    refs[t] = op_tools[t]
            scenarios[n] = sc
        out = self._detection.verify(rt.target_id, f"vf-{apply_id}", tools=[p.definition for p in cands],
                                     references=list(refs.values()), scenarios=scenarios)
        for p in cands:
            r = out["results"].get(p.definition["name"]) or {"result": "failed", "reason": "NO_RESULT"}
            p.verify = {k: r[k] for k in ("result", "calls", "reason") if k in r}
            result = r["result"]
            if result == "passed":
                continue
            if result == "unverified":
                if rt.no_scenario == "exclude":
                    p.outcome, p.reason = "excluded", "UNVERIFIED"
                elif "UNVERIFIED" not in p.reasons:
                    p.reasons.append("UNVERIFIED")
                    p.outcome = "pending"
                continue
            p.outcome, p.reason, p.error = "failed", "VERIFY_FAILED", r.get("error")

    # ──────────────────────────────────────────────────────────── 커밋
    def _commit(self, target_id, apply_id, plans, ops, state, spec_version, changeset, version, items) -> list[str]:
        now = self._clock()
        records = []
        for p in plans:
            if p.rec is None:
                continue
            tool_name = p.tool_name or (p.current or {}).get("name") or p.rec.tool_name
            if p.outcome == "removed":
                tool_name = None
            st = STATE_OF.get(p.outcome, "active")
            if p.outcome in ("pending", "failed", "rejected", "excluded") and p.current is not None:
                tool_name = p.current["name"]
            records.append(replace(
                p.rec, selection="removed" if p.rec.fragment is None else ("tool" if p.outcome not in
                                                                         ("excluded", "codeOwned") else p.outcome),
                reason=p.reason, tool_name=tool_name, state=st,
                last_error=p.error if p.outcome == "failed" else None, updated_at=now,
            ))
        new_reviews: list[str] = []
        with self._db.transaction() as tx:
            tx.save_operations(target_id, records)
            for i, p in enumerate(plans):
                if p.approved is not None:
                    tx.set_review_status(p.approved["reviewId"], "applied", applied_in=version)
                elif p.key is not None and p.outcome in ("added", "updated"):
                    tx.supersede_reviews(target_id, p.key[0], p.key[1])  # 다시 만들어 들어갔으니 옛 보류는 끝
                if p.outcome == "pending" and p.keep_review is None and p.reasons:
                    if p.key is not None:
                        tx.supersede_reviews(target_id, p.key[0], p.key[1])
                    rid = f"rv-{apply_id}-{i:03d}"
                    tx.add_review({
                        "reviewId": rid, "targetId": target_id, "applyId": apply_id, "kind": "tool",
                        "method": p.rec.method if p.rec else None, "path": p.rec.path if p.rec else None,
                        "operationId": p.rec.operation_id if p.rec else None, "toolName": p.tool_name,
                        "reasons": p.reasons, "definition": p.definition,
                        "previousRevision": (p.current or {}).get("revision"), "verify": p.verify,
                        "status": "pending", "fragmentSha256": p.rec.fragment_sha256 if p.rec else None,
                    })
                    new_reviews.append(rid)
            tx.add_items(apply_id, items)
            tx.save_state(TargetState(
                target_id=target_id, last_applied_spec_version=spec_version,
                last_change_set_id=changeset.change_set_id if changeset else (state.last_change_set_id if state else None),
                head_tool_version=version, current_tool_version=version, updated_at=now,
            ))
        return new_reviews

    # ──────────────────────────────────────────────────────────── 되돌리기
    def rollback(self, target_id: str, tool_version: str) -> dict[str, Any]:
        rt = self._runtime(target_id)
        if tool_version not in self._store.versions(target_id):
            raise ToolVersionNotFound(f"없는 tool 버전: {tool_version}")
        state = self._db.state(target_id)
        with self._db.transaction() as tx:  # DB가 정본: 바라는 포인터를 먼저 적는다
            tx.save_state(replace(state, current_tool_version=tool_version, updated_at=self._clock()))
        self._store.set_current(target_id, tool_version, updated_at=self._clock())
        tools = self._store.read(target_id, tool_version).tools
        index = self._index.sync(target_id, rt.rag_space, tools, tool_version=tool_version)
        return {"targetId": target_id, "currentToolVersion": tool_version, "headToolVersion": state.head_tool_version,
                "index": index}

    def recover(self, target_id: str) -> None:
        """시작할 때: 남은 임시 묶음을 지우고, 포인터를 DB의 바라는 값에 맞춘다 (설계 13.3절)."""
        self._store.clean_staging(target_id)
        state = self._db.state(target_id)
        if state and state.current_tool_version and self._store.current(target_id) != state.current_tool_version:
            self._store.set_current(target_id, state.current_tool_version, updated_at=self._clock())


def _record(target_id: str, oc: OperationChange, prev: OperationRecord | None, spec_version: str,
            now: str) -> OperationRecord:
    if oc.change == "removed":
        base = prev or OperationRecord(target_id, oc.method, oc.path, oc.operation_id, None, None, None, spec_version,
                                       "removed", None, None, "removed", None, now)
        return replace(base, fragment=None, fragment_sha256=None, structure_sha256=None, spec_version=spec_version,
                       selection="removed", updated_at=now)
    frag = oc.after
    return OperationRecord(
        target_id=target_id, method=oc.method, path=oc.path, operation_id=oc.operation_id, fragment=frag,
        fragment_sha256=fragment_sha(frag), structure_sha256=structure_sha(frag), spec_version=spec_version,
        selection="tool", reason=None, tool_name=prev.tool_name if prev else None,
        state=prev.state if prev else "new", last_error=None, updated_at=now,
    )


def _summary(items: list[dict[str, Any]]) -> dict[str, int]:
    keys = ("added", "updated", "removed", "unchanged", "pending", "failed", "excluded", "codeOwned")
    out = dict.fromkeys(keys, 0)
    for i in items:
        if i["outcome"] in out:
            out[i["outcome"]] += 1
    return out


def _verification_summary(plans: list[Plan]) -> dict[str, Any]:
    out = {"passed": 0, "failed": 0, "unverified": 0}
    for p in plans:
        r = (p.verify or {}).get("result")
        if r in out:
            out[r] += 1
    return out
