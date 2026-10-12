import pytest
from fastapi.testclient import TestClient

from a4w_core.errors import SpecFetchFailed, SpecHashMismatch, SpecInvalid, StorageError, UnsupportedSpecSource
from a4w_core.normalizers import IdentityNormalizer
from a4w_core.spec_parsers import YamlSpecParser
from a4w_detection.app import AppContext, create_app
from a4w_detection.diff import OpenApiDiffer
from a4w_detection.service import ChangeSetQuery, DetectService, TargetRuntime
from a4w_detection.textdiff import TextDiffer

from ..fakes import FakeArchive, FakeLock, FakeSource, FixedClock, MemoryStore, NoReview

V1 = b"openapi: 3.1.0\ninfo: {title: t, version: '1'}\npaths:\n  /a:\n    get: {operationId: a.get, responses: {}}\n"


def make(auth_mode="none", token=None, source=None, store=None, lock=None):
    store = store or MemoryStore()
    rt = TargetRuntime(source=source or FakeSource(V1), parser=YamlSpecParser(), normalizer=IdentityNormalizer(), policy=NoReview())
    svc = DetectService(targets={"wf": rt}, archive=FakeArchive(), store=store, lock=lock or FakeLock(),
                        differ=OpenApiDiffer(), text_differ=TextDiffer(3), clock=FixedClock())
    ctx = AppContext(detect=svc, query=ChangeSetQuery(targets={"wf"}, store=store), store=store,
                     auth_mode=auth_mode, api_token=token)
    return TestClient(create_app(ctx), raise_server_exceptions=False)


def test_detect_changed_then_unchanged():
    c = make()
    r = c.post("/v1/detect", json={"targetId": "wf"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "changed" and body["targetId"] == "wf"
    assert body["changeSetId"].startswith("cs-") and body["fromVersion"] is None
    assert body["summary"] == {"added": 1, "removed": 0, "modified": 0, "docOnly": 0, "documentChanges": 2}
    assert body["needsReview"] is False and body["reviewReasons"] == []
    r2 = c.post("/v1/detect", json={"targetId": "wf"}).json()
    assert r2 == {"status": "unchanged", "targetId": "wf", "currentVersion": body["toVersion"]}


@pytest.mark.parametrize("payload", [{}, {"targetId": 1}, {"targetId": "wf", "extra": 1}, None])
def test_bad_request_body(payload):
    c = make()
    r = c.post("/v1/detect", json=payload) if payload is not None else c.post("/v1/detect", content=b"x")
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unknown_target_404():
    r = make().post("/v1/detect", json={"targetId": "nope"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "TARGET_NOT_FOUND"


def test_in_progress_409():
    lock = FakeLock(); lock.busy.add("wf")
    r = make(lock=lock).post("/v1/detect", json={"targetId": "wf"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "DETECT_IN_PROGRESS"


class Raising(FakeSource):
    def __init__(self, exc):
        super().__init__(V1)
        self.exc = exc

    def fetch_hash(self):
        raise self.exc


@pytest.mark.parametrize("exc,status", [
    (SpecFetchFailed("x"), 502), (SpecHashMismatch("x"), 502), (SpecInvalid("x"), 502),
    (UnsupportedSpecSource("x"), 501), (StorageError("x"), 500), (RuntimeError("secret detail"), 500),
])
def test_error_mapping(exc, status):
    r = make(source=Raising(exc)).post("/v1/detect", json={"targetId": "wf"})
    assert r.status_code == status
    err = r.json()["error"]
    assert err["code"] == (getattr(exc, "code", None) or "INTERNAL_ERROR")
    assert "secret detail" not in err["message"]


def test_get_changeset_and_text_diff_flag():
    c = make()
    cs_id = c.post("/v1/detect", json={"targetId": "wf"}).json()["changeSetId"]
    body = c.get(f"/v1/changesets/{cs_id}", params={"targetId": "wf"}).json()
    assert body["changeSetId"] == cs_id and "textDiff" not in body
    with_diff = c.get(f"/v1/changesets/{cs_id}", params={"targetId": "wf", "includeTextDiff": "true"}).json()
    assert with_diff["textDiff"].startswith("--- ")


def test_get_changeset_errors():
    c = make()
    assert c.get("/v1/changesets/cs-x", params={"targetId": "wf"}).json()["error"]["code"] == "CHANGESET_NOT_FOUND"
    assert c.get("/v1/changesets/cs-x", params={"targetId": "nope"}).status_code == 404
    assert c.get("/v1/changesets/cs-x").status_code == 400


def test_bearer_auth():
    c = make(auth_mode="bearer", token="t0k")
    assert c.post("/v1/detect", json={"targetId": "wf"}).json()["error"]["code"] == "UNAUTHORIZED"
    assert c.post("/v1/detect", json={"targetId": "wf"}, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.post("/v1/detect", json={"targetId": "wf"}, headers={"Authorization": "Bearer t0k"}).status_code == 200
    assert c.get("/v1/changesets/cs-x", params={"targetId": "wf"}).status_code == 401
    assert c.get("/health").status_code == 200
    assert c.get("/openapi.json").status_code == 200


def test_health_reports_storage_error():
    class Broken(MemoryStore):
        def check(self):
            raise StorageError("x")
    r = make(store=Broken()).get("/health")
    assert r.status_code == 503 and r.json() == {"status": "error", "specStore": "error"}


def test_health_ok():
    assert make().get("/health").json() == {"status": "ok", "specStore": "ok"}


def test_openapi_lists_api():
    paths = make().get("/openapi.json").json()["paths"]
    assert {"/v1/detect", "/v1/changesets/{changeSetId}", "/health"} <= set(paths)
