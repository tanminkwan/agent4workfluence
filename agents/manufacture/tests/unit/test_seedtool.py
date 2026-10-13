"""seed 도구 (Phase 2 설계 7.3절): 사람 글 + 기계 골격 → seed JSON. 저장소의 seed가 시험 스택 명세와 맞는지 고정한다."""

from __future__ import annotations

import json
import shutil

import pytest

from a4w_manufacture.catalog import FileCatalog
from a4w_manufacture.seedtool import SeedError, build_seeds, main

from ..specs import FIXTURES, catalog_dir

SPEC = FIXTURES / "openapi-75ops.yaml"
ARGS = ["--spec", str(SPEC), "--limits", "description=600,summary=120,example=60", "--max-visible", "4",
        "--max-chars", "8000", "--content-types", "application/json,multipart/form-data"]


def copy_catalog(tmp_path):
    dest = tmp_path / "catalog"
    shutil.copytree(str(catalog_dir()), dest)
    return dest


def test_committed_seeds_match_the_spec():
    """저장소의 seed가 지금 명세·규칙·글로 다시 만든 것과 같다 (AC-1의 전제)."""
    assert main(["check", "--catalog", str(catalog_dir()), *ARGS]) == 0


def test_build_writes_all_tool_operations(tmp_path):
    cat = copy_catalog(tmp_path)
    report = build_seeds(SPEC, cat, limits="description=600,summary=120,example=60", max_visible=4, max_chars=8000,
                         content_types=["application/json", "multipart/form-data"])
    assert report["tools"] == 34 and report["composites"] == 3
    names = {p.stem for p in (cat / "seed" / "tools").glob("*.json")}
    assert {"add_comment", "read_page", "search_pages", "update_page", "delete_page"} <= names
    seeds = FileCatalog(cat)
    t = seeds.seed_for("pages.update")
    assert list(t["inputSchema"]["properties"]) == ["id", "baseVersionNo", "body", "title"]
    assert t["source"]["specVersion"] == report["specVersion"]
    for s in seeds.seeds():
        assert len(s["inputSchema"]["properties"]) <= 4, s["name"]


def test_missing_authoring_is_an_error(tmp_path):
    cat = copy_catalog(tmp_path)
    text = (cat / "seed" / "authoring.yaml").read_text(encoding="utf-8")
    (cat / "seed" / "authoring.yaml").write_text(text.replace("spaces.get:", "spaces_get_typo:"), encoding="utf-8")
    with pytest.raises(SeedError) as e:
        build_seeds(SPEC, cat, limits="description=600,summary=120,example=60", max_visible=4, max_chars=8000,
                    content_types=["application/json"])
    assert "spaces.get" in str(e.value)


def test_check_fails_when_seed_is_stale(tmp_path):
    cat = copy_catalog(tmp_path)
    f = cat / "seed" / "tools" / "read_page.json"
    d = json.loads(f.read_text())
    d["title"] = "손으로 바꾼 제목"
    f.write_text(json.dumps(d))
    assert main(["check", "--catalog", str(cat), *ARGS]) == 1


def test_queries_and_scenarios_name_real_tools():
    cat = FileCatalog(catalog_dir())
    names = {t["name"] for t in cat.seeds()} | {c["name"] for c in cat.composites()}
    for q in cat.queries():
        assert set(q["expect"]) <= names, q
    for p in (catalog_dir() / "scenarios").iterdir():
        assert p.name[: -len(".yaml")] in names, p.name
