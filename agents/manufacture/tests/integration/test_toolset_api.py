"""tool 정의 조회 API (Phase 3 설계 5절, AC-T1·T2)."""

from __future__ import annotations

import json
import shutil

from .test_api import build


def applied(tmp_path, *changesets):
    c, starter, _ = build(tmp_path)
    versions = []
    for cs in changesets:
        r = c.post("/v1/apply", json={"targetId": "wf", "changeSetId": cs})
        starter.run_all()
        versions.append(c.get(f"/v1/applies/{r.json()['applyId']}").json()["toolVersion"])
    return c, starter, versions


def two_versions(tmp_path):
    """cs-1 반영 → cs-2(삭제, 보류) → 승인 → 정리 반영 = 두 번째 버전."""
    c, starter, (v1,) = applied(tmp_path, "cs-1")
    c.post("/v1/apply", json={"targetId": "wf", "changeSetId": "cs-2"})
    starter.run_all()
    (pend,) = c.get("/v1/reviews", params={"targetId": "wf", "status": "pending"}).json()["reviews"]
    c.post(f"/v1/reviews/{pend['reviewId']}/decision", json={"decision": "approve"})
    r = c.post("/v1/apply", json={"targetId": "wf"})
    starter.run_all()
    v2 = c.get(f"/v1/applies/{r.json()['applyId']}").json()["toolVersion"]
    assert v1 != v2
    return c, v1, v2


def test_current_version_with_etag_and_304(tmp_path):
    c, _, (v1,) = applied(tmp_path, "cs-1")
    r = c.get("/v1/targets/wf/toolset/current")
    assert r.status_code == 200
    body = r.json()
    assert body["targetId"] == "wf" and body["toolVersion"] == v1 and body["updatedAt"]
    assert r.headers["etag"] == f'"{v1}"'
    again = c.get("/v1/targets/wf/toolset/current", headers={"If-None-Match": f'"{v1}"'})
    assert again.status_code == 304 and again.content == b""
    other = c.get("/v1/targets/wf/toolset/current", headers={"If-None-Match": '"tv-000000-00000000"'})
    assert other.status_code == 200


def test_current_toolset_is_the_stored_bundle(tmp_path):
    c, _, (v1,) = applied(tmp_path, "cs-1")
    r = c.get("/v1/targets/wf/toolset")
    assert r.status_code == 200
    body = r.json()
    vdir = tmp_path / "store" / "targets" / "wf" / "versions" / v1
    manifest = json.loads((vdir / "manifest.json").read_text(encoding="utf-8"))
    assert body["targetId"] == "wf" and body["toolVersion"] == v1
    assert body["manifest"] == manifest
    assert set(body["tools"]) == {t["name"] for t in manifest["tools"]}
    for entry in manifest["tools"]:
        assert body["tools"][entry["name"]] == json.loads((vdir / entry["file"]).read_text(encoding="utf-8"))
    assert r.headers["etag"] == f'"{v1}"'


def test_version_toolset_is_immutable_and_cacheable(tmp_path):
    c, v1, v2 = two_versions(tmp_path)
    r = c.get(f"/v1/targets/wf/toolsets/{v1}")
    assert r.status_code == 200 and r.json()["toolVersion"] == v1
    assert "immutable" in r.headers["cache-control"]
    assert c.get("/v1/targets/wf/toolset/current").json()["toolVersion"] == v2


def test_rollback_moves_current(tmp_path):
    c, v1, v2 = two_versions(tmp_path)
    assert c.get("/v1/targets/wf/toolset/current").json()["toolVersion"] == v2
    c.post("/v1/rollback", json={"targetId": "wf", "toolVersion": v1})
    assert c.get("/v1/targets/wf/toolset/current").json()["toolVersion"] == v1
    assert c.get("/v1/targets/wf/toolset").json()["toolVersion"] == v1


def test_staging_is_never_served(tmp_path):
    c, _, (v1,) = applied(tmp_path, "cs-1")
    staging = tmp_path / "store" / "targets" / "wf" / "staging" / "ap-x"
    shutil.copytree(tmp_path / "store" / "targets" / "wf" / "versions" / v1, staging)
    assert c.get("/v1/targets/wf/toolset").json()["toolVersion"] == v1
    assert c.get("/v1/targets/wf/toolsets/ap-x").status_code == 400


def test_errors(tmp_path):
    c, _, _ = build(tmp_path)
    r = c.get("/v1/targets/nope/toolset/current")
    assert r.status_code == 404 and r.json()["error"]["code"] == "TARGET_NOT_FOUND"
    r = c.get("/v1/targets/wf/toolset/current")
    assert r.status_code == 404 and r.json()["error"]["code"] == "TOOLSET_NOT_FOUND"
    r = c.get("/v1/targets/wf/toolset")
    assert r.status_code == 404 and r.json()["error"]["code"] == "TOOLSET_NOT_FOUND"
    r = c.get("/v1/targets/wf/toolsets/tv-000009-aaaaaaaa")
    assert r.status_code == 404 and r.json()["error"]["code"] == "TOOL_VERSION_NOT_FOUND"
    r = c.get("/v1/targets/wf/toolsets/not-a-version")
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_broken_bundle_is_storage_error(tmp_path):
    c, _, (v1,) = applied(tmp_path, "cs-1")
    manifest = tmp_path / "store" / "targets" / "wf" / "versions" / v1 / "manifest.json"
    entry = json.loads(manifest.read_text(encoding="utf-8"))["tools"][0]
    (manifest.parent / entry["file"]).write_text("{}", encoding="utf-8")
    r = c.get("/v1/targets/wf/toolset")
    assert r.status_code == 500 and r.json()["error"]["code"] == "STORAGE_ERROR"


def test_bearer_auth_applies(tmp_path):
    c, _, _ = build(tmp_path, auth="bearer")
    assert c.get("/v1/targets/wf/toolset/current").status_code == 401
    r = c.get("/v1/targets/wf/toolset/current", headers={"Authorization": "Bearer tk"})
    assert r.status_code == 404  # 인증은 통과, 아직 버전 없음


def test_listed_in_openapi(tmp_path):
    c, _, _ = build(tmp_path)
    paths = c.get("/openapi.json").json()["paths"]
    assert {"/v1/targets/{targetId}/toolset/current", "/v1/targets/{targetId}/toolset",
            "/v1/targets/{targetId}/toolsets/{toolVersion}"} <= set(paths)
