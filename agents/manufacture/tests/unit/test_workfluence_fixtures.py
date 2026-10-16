"""실제 카탈로그 + 실제 명세 고정 자료 (AC-1, AC-3a). 외부(Detection·LLM·RAG)는 가짜."""

from __future__ import annotations

from collections import Counter

from a4w_clients.rag.base import RagSpace
from a4w_manufacture.catalog import FileCatalog
from a4w_manufacture.index.sync import IndexSync
from a4w_manufacture.service import ApplyService, TargetRuntime
from a4w_manufacture.storage.bundles import FileToolStore
from a4w_manufacture.storage.sqlite_db import SqliteManufactureDb
from a4w_manufacture.transform.openapi3 import OpenApi3Transformer

from ..apply_fakes import FakeAssistant, FakeDetection, FakeRag, changeset
from ..specs import SEED_SPEC, catalog_dir, fragments

DEFAULT = ("read", "history", "communication", "authoring")


def env(tmp_path, groups=DEFAULT):
    det = FakeDetection()
    db = SqliteManufactureDb(tmp_path / "m.db")
    store = FileToolStore(tmp_path / "store")
    ticks = iter(range(100000))
    clock = lambda: f"2026-10-13T00:00:{next(ticks) % 60:02d}Z"  # noqa: E731
    rt = TargetRuntime(target_id="workfluence", catalog=FileCatalog(catalog_dir()), enabled_groups=groups,
                       transformer=OpenApi3Transformer(content_types=["application/json", "multipart/form-data"]),
                       max_chars=8000, no_scenario="pending",
                       rag_space=RagSpace("a4w_tools_workfluence", "a4w-tools-workfluence", "d"))
    svc = ApplyService(targets={"workfluence": rt}, detection=det, assistant=FakeAssistant(), store=store, db=db,
                       index=IndexSync(FakeRag(), db, content_max_chars=600, list_limit=1000, clock=clock),
                       clock=clock, llm_max_calls=10, example_count=3)

    def apply(cs, apply_id):
        db.create_apply({"applyId": apply_id, "targetId": "workfluence", "changeSetId": cs.change_set_id,
                         "status": "running", "startedAt": clock()})
        return svc.run("workfluence", apply_id=apply_id, changeset=cs)

    return apply, det, store


def test_first_apply_of_real_spec_uses_seeds_only(tmp_path):
    apply, det, store = env(tmp_path)
    ops = fragments(SEED_SPEC)
    r = apply(changeset("cs-1", {}, ops, None, "4283c9bf"), "ap-1")
    assert r["llm"]["calls"] == 0
    served = store.read("workfluence", store.current("workfluence")).tools
    assert len(served) == 36                           # 단순 33(스페이스 6 포함) + 상위 3
    assert all(t["origin"] == "seed" for t in served.values())
    assert len(det.verify_calls) == 1 and len(det.verify_calls[0]["tools"]) == 36


def test_first_apply_counts_exactly(tmp_path):
    apply, _, store = env(tmp_path)
    r = apply(changeset("cs-1", {}, fragments(SEED_SPEC), None, "4283c9bf"), "ap-1")
    by = Counter((i["outcome"], i.get("reason")) for i in r["items"])
    assert by[("added", None)] == 36                    # 단순 33 + 상위 3
    assert by[("excluded", "ADMIN")] == 12
    assert by[("excluded", "STRUCTURE_CHANGE")] == 6           # 분류·템플릿 (스페이스는 설계 23절로 tool)
    assert by[("excluded", "NOT_NEEDED")] == 12            # 내보내기·휴지통 3 + 채팅 9 (채팅은 tool 대상이 아니다)
    assert by[("excluded", "SPEC_META")] == 3
    assert by[("excluded", "GROUP_DISABLED")] == 9             # 위험 7 + 스페이스 지우기·상태
    assert ("codeOwned", None) not in by
    assert r["summary"]["added"] == 36 and r["summary"]["excluded"] == 42 and r["summary"]["codeOwned"] == 0


def test_66_to_75_adds_only_chat_operations_and_changes_nothing(tmp_path):
    """66 → 75의 차이는 채팅 9개 추가뿐이다. 채팅은 tool 대상이 아니라 tool은 바뀌지 않고, 따로 알리는 것도 없다 (AC-3a)."""
    before, after = fragments("openapi-66ops.yaml"), fragments("openapi-75ops.yaml")
    cs = changeset("cs-2", before, after, "v66", "v75")
    assert Counter(o.change for o in cs.operations) == {"added": 9}
    apply, _, store = env(tmp_path)
    seeded = fragments(SEED_SPEC)                      # 반영은 seed가 맞춘 명세로 한다 (채팅만 뺀 것 → 전부)
    base = {k: v for k, v in seeded.items() if not k.startswith("chat.")}
    r1 = apply(changeset("cs-1", {}, base, None, "v66"), "ap-1")
    r2 = apply(changeset("cs-2", base, seeded, "v66", "v75"), "ap-2")
    assert not r2.get("codeOwnedChanges")
    assert r2["status"] == "noop" and r2["toolVersion"] == r1["toolVersion"]


def test_danger_enabled_holds_nine_for_review(tmp_path):
    apply, _, store = env(tmp_path, groups=DEFAULT + ("danger",))
    r = apply(changeset("cs-1", {}, fragments(SEED_SPEC), None, "4283c9bf"), "ap-1")
    assert sorted(p["tool"] for p in r["pendingReview"]) == sorted([
        "delete_page", "move_page", "restore_page_version", "upload_attachment", "delete_attachment",
        "list_trashed_pages", "restore_trashed_page", "delete_space", "set_space_status"])
    assert all(p["reasons"] == ["DANGER_ADDED"] for p in r["pendingReview"])


def test_by_title_composites_pass_an_optional_space_to_the_page_search():
    """제목으로 찾는 상위 tool도 다른 스페이스에서 찾는다 (설계 26절, 2026-10-16 채팅 시험). 비우면 기본 스페이스."""
    from a4w_manufacture.catalog import FileCatalog
    comps = {c["name"]: c for c in FileCatalog(catalog_dir()).composites()}
    for name in ("comment_on_page_by_title", "label_page_by_title", "read_page_by_title"):
        c = comps[name]
        assert "space" in c["inputSchema"]["properties"] and "space" not in c["inputSchema"]["required"], name
        find = c["steps"][0]
        assert find["tool"] == "search_pages" and find["args"]["space"] == {"$input": "space"}, name
