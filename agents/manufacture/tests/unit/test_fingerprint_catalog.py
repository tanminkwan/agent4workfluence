"""지문과 카탈로그(seed·시나리오·질의) 읽기 (Phase 2 설계 7.2·7.3절)."""

from __future__ import annotations

import copy
import json

from a4w_manufacture.catalog import FileCatalog
from a4w_manufacture.fingerprint import fragment_sha, input_sha, structure_sha
from a4w_manufacture.transform.openapi3 import OpenApi3Transformer

from ..specs import fragment


def test_fragment_and_structure_sha():
    f = fragment("comments.create")
    g = copy.deepcopy(f)
    g["description"] = "설명만 바꿈"
    assert fragment_sha(f) != fragment_sha(g)
    assert structure_sha(f) == structure_sha(g)
    h = copy.deepcopy(f)
    h["parameters"] = []
    assert structure_sha(f) != structure_sha(h)
    assert len(fragment_sha(f)) == 64


def test_input_sha_ignores_description():
    t = OpenApi3Transformer(content_types=["application/json"])
    a = t.skeleton(fragment("comments.create"), fixed={}).inputs[0]
    f = copy.deepcopy(fragment("comments.create"))
    f["parameters"][0]["description"] = "다른 설명"
    b = t.skeleton(f, fixed={}).inputs[0]
    assert input_sha(a) == input_sha(b)


def write_catalog(root, tools=(), composites=(), scenarios=None, queries=None):
    (root / "seed" / "tools").mkdir(parents=True)
    (root / "seed" / "composites").mkdir(parents=True)
    (root / "scenarios").mkdir()
    (root / "rules.yaml").write_text("version: 1\ngroups: {read: {risk: read}}\nfallback: {default: read}\n")
    for t in tools:
        (root / "seed" / "tools" / f"{t['name']}.json").write_text(json.dumps(t))
    for c in composites:
        (root / "seed" / "composites" / f"{c['name']}.json").write_text(json.dumps(c))
    for name, sc in (scenarios or {}).items():
        (root / "scenarios" / f"{name}.yaml").write_text(json.dumps(sc))
    (root / "queries.yaml").write_text(json.dumps(queries or {"queries": []}))


def seed(name, op_id, sha="a" * 64, struct="b" * 64):
    return {"name": name, "kind": "operation", "source": {"operationId": op_id, "fragmentSha256": sha,
                                                          "structureSha256": struct}}


def test_catalog_lookup_by_operation_and_fingerprint(tmp_path):
    write_catalog(tmp_path, tools=[seed("add_comment", "comments.create")])
    cat = FileCatalog(tmp_path)
    assert cat.seed_for("comments.create")["name"] == "add_comment"
    assert cat.seed_for("nope") is None
    assert cat.seed_match("comments.create", "a" * 64, "b" * 64) == "same"
    assert cat.seed_match("comments.create", "c" * 64, "b" * 64) == "doc_only"
    assert cat.seed_match("comments.create", "c" * 64, "d" * 64) == "changed"
    assert cat.seed_match("nope", "a" * 64, "b" * 64) == "none"


def test_catalog_reads_scenarios_composites_queries(tmp_path):
    write_catalog(tmp_path, composites=[{"name": "c1", "kind": "composite"}],
                  scenarios={"add_comment": {"call": {"args": {}}}},
                  queries={"queries": [{"query": "댓글 달아줘", "expect": ["add_comment"]}]})
    cat = FileCatalog(tmp_path)
    assert [c["name"] for c in cat.composites()] == ["c1"]
    assert cat.scenario("add_comment") == {"call": {"args": {}}}
    assert cat.scenario("x") is None
    assert cat.queries() == [{"query": "댓글 달아줘", "expect": ["add_comment"]}]
    assert cat.rules()["version"] == 1


def test_seed_examples_prefer_same_group_then_method(tmp_path):
    tools = [
        dict(seed("a_read", "a.x"), http={"method": "GET"}, group="communication"),
        dict(seed("b_write", "b.x"), http={"method": "POST"}, group="authoring"),
        dict(seed("c_write", "c.x"), http={"method": "POST"}, group="communication"),
        dict(seed("d_read", "d.x"), http={"method": "GET"}, group="read"),
    ]
    write_catalog(tmp_path, tools=tools)
    cat = FileCatalog(tmp_path)
    picked = cat.examples(group="communication", method="POST", count=3, exclude="c_write")
    assert [t["name"] for t in picked] == ["a_read", "b_write", "d_read"]


def test_workfluence_catalog_loads():
    from ..specs import catalog_dir

    cat = FileCatalog(catalog_dir())
    assert "groups" in cat.rules()
