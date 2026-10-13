"""ApplyService 시험용: 작은 카탈로그(seed), 변경분 만들기, 가짜 Detection·LLM·RAG."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import yaml

from a4w_clients.rag.base import RagSpace, RagSpaceRef, RagStore
from a4w_core.models import ChangeSet, OperationChange, ReviewResult
from a4w_core.openapi.canonical import canonical_json
from a4w_manufacture.assembly import assemble, parse_authoring
from a4w_manufacture.assist.assistant import AssistFailed, Authored
from a4w_manufacture.fingerprint import fragment_sha, structure_sha
from a4w_manufacture.selection import Selector
from a4w_manufacture.transform.openapi3 import OpenApi3Transformer

from .specs import fragments, rules

T = OpenApi3Transformer(content_types=["application/json"])
OPS = ["comments.create", "pages.get", "search.find", "pages.delete", "users.list", "chat.send"]


def frags(*op_ids):
    all_ = fragments()
    return {o: copy.deepcopy(all_[o]) for o in op_ids}


AUTHORING = {
    "comments.create": {"name": "add_comment", "title": "페이지에 댓글 달기", "description": "페이지에 댓글을 단다.",
                        "inputs": {"pageId": {"description": "search_pages 결과의 pageId"},
                                   "body": {"description": "댓글 본문 (마크다운)"}},
                        "index": {"summary": "페이지에 댓글을 남긴다",
                                  "examples": ["회의록에 댓글 달아줘", "답글 남겨줘", "확인했다고 댓글 써줘"]}},
    "pages.get": {"name": "read_page", "title": "페이지 읽기", "description": "페이지 본문을 읽는다.",
                  "inputs": {"id": {"description": "search_pages 결과의 pageId"}},
                  "responseFields": ["id", "title", "body", "currentVersionNo"],
                  "index": {"summary": "페이지 본문을 읽는다", "examples": ["회의록 읽어줘", "공지 내용 보여줘", "문서 열어줘"]}},
    "search.find": {"name": "search_pages", "title": "페이지 검색", "description": "제목·본문으로 페이지를 찾는다.",
                    "inputs": {"q": {"description": "검색어 (두 글자 이상)"}},
                    "responseFields": ["items[].pageId", "items[].title"],
                    "index": {"summary": "페이지를 검색한다", "examples": ["회의록 찾아줘", "보안 문서 검색", "공지 찾아줘"]}},
    "pages.delete": {"name": "delete_page", "title": "페이지 지우기", "description": "페이지를 휴지통으로 보낸다.",
                     "inputs": {"id": {"description": "지울 페이지 id"}},
                     "index": {"summary": "페이지를 지운다", "examples": ["이 페이지 지워줘", "초안 삭제해줘", "문서 버려줘"]}},
}
GROUPS = {"comments.create": "communication", "pages.get": "read", "search.find": "read", "pages.delete": "danger"}


def seed_definition(op_id, frag=None):
    frag = frag or frags(op_id)[op_id]
    sel = Selector(rules(), enabled_groups=["read", "history", "communication", "authoring", "danger"],
                   token_scopes={"read", "write", "admin"})
    sk = T.skeleton(frag, fixed=sel.fixed_values(frag))
    raw = AUTHORING[op_id]
    s = sel.select(frag)
    source = {"operationId": op_id, "method": frag["method"], "path": frag["path"], "specVersion": "seed",
              "fragmentSha256": fragment_sha(frag), "structureSha256": structure_sha(frag)}
    return assemble(sk, parse_authoring(raw, max_chars=4000), group=s.group, risk=s.risk, origin="seed", source=source)


def composite_seed():
    return {
        "schema": "a4w.tool/v1", "kind": "composite", "name": "comment_on_page_by_title",
        "title": "제목으로 찾아 댓글 달기", "description": "제목으로 페이지를 하나 찾아 댓글을 단다.",
        "inputSchema": {"type": "object", "properties": {"title": {"type": "string", "description": "페이지 제목"},
                                                          "body": {"type": "string", "description": "댓글 본문"}},
                        "required": ["title", "body"], "additionalProperties": False},
        "steps": [
            {"id": "find", "tool": "search_pages", "args": {"q": {"$input": "title"}},
             "expect": {"pointer": "/items", "count": "one"}},
            {"id": "comment", "tool": "add_comment",
             "args": {"pageId": {"$step": "find", "pointer": "/items/0/pageId"}, "body": {"$input": "body"}}},
        ],
        "output": {"$step": "comment"},
        "risk": "write", "scopes": ["write"], "group": "communication",
        "index": {"summary": "제목으로 찾은 페이지에 댓글", "examples": ["회의록에 댓글 달아줘", "공지에 의견 남겨줘", "보고서에 답글"]},
        "origin": "seed", "revision": "r-000000000000",
    }


def write_catalog(root: Path, seeds=("comments.create", "pages.get", "search.find", "pages.delete"), composite=True):
    (root / "seed" / "tools").mkdir(parents=True, exist_ok=True)
    (root / "seed" / "composites").mkdir(parents=True, exist_ok=True)
    (root / "scenarios").mkdir(exist_ok=True)
    (root / "rules.yaml").write_text(yaml.safe_dump(rules(), allow_unicode=True), encoding="utf-8")
    for op in seeds:
        d = seed_definition(op)
        (root / "seed" / "tools" / f"{d['name']}.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    if composite:
        (root / "seed" / "composites" / "comment_on_page_by_title.json").write_text(
            json.dumps(composite_seed(), ensure_ascii=False), encoding="utf-8")
    (root / "scenarios" / "add_comment.yaml").write_text(yaml.safe_dump(
        {"call": {"args": {"pageId": "${sandbox.pageId}", "body": "검증 ${run.id}"}}, "expect": {"ok": True}}),
        encoding="utf-8")
    (root / "queries.yaml").write_text("queries: []\n")
    return root


def changeset(cs_id, before: dict, after: dict, from_version, to_version):
    """두 동작 묶음(operationId → 조각)의 차이 (Detection의 Workfluence v1 정책을 흉내)."""
    ops = []
    reasons = set()
    keys = sorted(set(before) | set(after), key=lambda o: ((after.get(o) or before.get(o))["path"],
                                                           (after.get(o) or before.get(o))["method"]))
    for o in keys:
        b, a = before.get(o), after.get(o)
        if b is None:
            ops.append(OperationChange(a["method"], a["path"], o, "added", after=a))
        elif a is None:
            ops.append(OperationChange(b["method"], b["path"], o, "removed", before=b))
            reasons.add("OPERATION_REMOVED")
        elif canonical_json(a) != canonical_json(b):
            doc_only = structure_sha(a) == structure_sha(b)
            ops.append(OperationChange(a["method"], a["path"], o, "modified", before=b, after=a,
                                       changed_fields=("x",), doc_only=doc_only))
            if not doc_only:
                reasons.add("OPERATION_CHANGED")
    return ChangeSet(change_set_id=cs_id, target_id="wf", from_version=from_version, to_version=to_version,
                     created_at="t", operations=tuple(ops), document_changes=(),
                     review=ReviewResult(bool(reasons), tuple(sorted(reasons))), text_diff="")


class FakeDetection:
    def __init__(self, scopes=frozenset({"read", "write", "admin"})):
        self._scopes = scopes
        self.fail = set()          # 검증 실패시킬 tool 이름
        self.unverified = set()
        self.fail_all = False
        self.verify_calls = []

    def scopes(self, target_id):
        return self._scopes

    def verify(self, target_id, verification_id, *, tools, references, scenarios):
        from a4w_manufacture.errors import VerifyFailedAll

        self.verify_calls.append({"id": verification_id, "tools": [t["name"] for t in tools],
                                  "references": [t["name"] for t in references], "scenarios": sorted(scenarios)})
        if self.fail_all:
            raise VerifyFailedAll("x", details={"code": "SANDBOX_UNAVAILABLE"})
        results = {}
        for t in tools:
            n = t["name"]
            r = "failed" if n in self.fail else "unverified" if n in self.unverified else "passed"
            results[n] = {"tool": n, "revision": t["revision"], "result": r, "calls": 1, "elapsedMs": 1}
            if r == "failed":
                results[n]["error"] = {"code": "TARGET_ERROR", "targetCode": "NOT_FOUND"}
        return {"results": results, "sandbox": {"prepared": True, "cleanup": "ok", "cleanupFailures": []}}


class FakeAssistant:
    """operationId별 출력. 출력이 Exception이면 실패시킨다."""

    def __init__(self, outputs=None):
        self.outputs = dict(outputs or {})
        self.calls = []

    def with_budget(self, budget):
        return self

    def author(self, task, skeleton, fragment, *, previous, taken_names, examples):
        self.calls.append((task, skeleton.operation_id, (previous or {}).get("name")))
        out = self.outputs.get(skeleton.operation_id)
        if out is None:
            raise AssistFailed("LLM_UNAVAILABLE", "출력 없음")
        if isinstance(out, Exception):
            raise out
        raw = copy.deepcopy(out)
        if previous is not None:
            raw["name"] = previous["name"]
        return Authored(raw=raw, origin="llm-doc" if task == "redescribe" else "llm", calls=1,
                        usage={"promptTokens": 1, "completionTokens": 1, "reasoningTokens": 0})


class FakeRag(RagStore):
    def __init__(self):
        self.points = {}

    def ensure_space(self, space: RagSpace):
        return RagSpaceRef(space.collection, 1)

    def upsert(self, ref, doc):
        self.points[doc.id] = doc.source

    def delete(self, ref, doc_id):
        self.points.pop(doc_id, None)

    def list_ids(self, ref, *, limit):
        return dict(self.points)

    def search(self, ref, query, *, limit):
        return []
