"""검증·타겟 정보 API (Phase 2 설계 11.3·11.5절). Phase 1 API는 그대로다."""

from __future__ import annotations

from fastapi.testclient import TestClient

from a4w_core.errors import VerifyInProgress
from a4w_core.locking import FileTargetLock
from a4w_detection.app import AppContext, create_app
from a4w_detection.profile import TargetProfileService
from a4w_detection.verify.runner import VerificationRunner
from a4w_detection.verify.service import VerificationService, VerifyRuntime
from a4w_detection.verify.store import SqliteVerificationStore

from ..fakes import FixedClock, MemoryStore
from ..unit.test_verify_service import ManualStarter, Scopes
from ..verify_fakes import SPEC_OPS, FakeSandbox, list_users, make_runtime_parts, read_page


def client(tmp_path, with_verify=True):
    _, executor, caller = make_runtime_parts()
    sandbox = FakeSandbox()
    rt = VerifyRuntime(runner=VerificationRunner(executor=executor, caller=caller, sandbox=sandbox),
                       scope_provider=Scopes(), token="tok", sandbox_name="fake",
                       context_keys=sandbox.context_keys(), deny_scopes=frozenset({"admin"}))
    starter = ManualStarter()
    svc = VerificationService(
        known_targets={"wf"}, runtimes={"wf": rt}, store=SqliteVerificationStore(tmp_path / "v.db"),
        lock=FileTargetLock(tmp_path / "locks", busy=lambda k: VerifyInProgress(k)),
        spec_operations=lambda t: SPEC_OPS, clock=FixedClock(), max_tools=10, keep_days=30, starter=starter,
    )
    ctx = AppContext(detect=None, query=None, store=MemoryStore(), auth_mode="none", api_token=None,
                     verify=svc if with_verify else None,
                     profile=TargetProfileService(known_targets={"wf"}, runtimes=svc.runtimes) if with_verify else None)
    return TestClient(create_app(ctx), raise_server_exceptions=False), starter


def body(vid="vf-1", tools=None):
    return {"targetId": "wf", "verificationId": vid, "tools": tools or [read_page()]}


def test_submit_202_then_poll(tmp_path):
    c, starter = client(tmp_path)
    r = c.post("/v1/verifications", json=body())
    assert r.status_code == 202
    assert r.json() == {"verificationId": "vf-1", "targetId": "wf", "status": "running"}
    starter.run_all()
    g = c.get("/v1/verifications/vf-1", params={"targetId": "wf"})
    assert g.status_code == 200
    assert g.json()["status"] == "done"
    assert g.json()["results"][0]["tool"] == "read_page"


def test_resubmit_returns_200_with_state(tmp_path):
    c, starter = client(tmp_path)
    c.post("/v1/verifications", json=body())
    starter.run_all()
    r = c.post("/v1/verifications", json=body())
    assert r.status_code == 200 and r.json()["status"] == "done"


def test_invalid_request_400_with_details(tmp_path):
    c, _ = client(tmp_path)
    r = c.post("/v1/verifications", json=body(tools=[list_users()]))
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "VERIFY_REQUEST_INVALID"
    assert err["details"][0]["reason"] == "DENIED_SCOPE"


def test_conflict_409(tmp_path):
    c, _ = client(tmp_path)
    c.post("/v1/verifications", json=body("vf-1"))
    r = c.post("/v1/verifications", json=body("vf-2"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "VERIFY_IN_PROGRESS"


def test_bad_verification_id_is_validation_error(tmp_path):
    c, _ = client(tmp_path)
    assert c.post("/v1/verifications", json=body(vid="has space")).status_code == 400


def test_not_found(tmp_path):
    c, _ = client(tmp_path)
    r = c.get("/v1/verifications/vf-9", params={"targetId": "wf"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "VERIFICATION_NOT_FOUND"


def test_profile(tmp_path):
    c, _ = client(tmp_path)
    r = c.get("/v1/targets/wf/profile")
    assert r.status_code == 200
    assert r.json()["tokenScopes"] == ["admin", "read", "write"]
    assert c.get("/v1/targets/nope/profile").status_code == 404


def test_without_verification_configured_is_501(tmp_path):
    c, _ = client(tmp_path, with_verify=False)
    assert c.post("/v1/verifications", json=body()).status_code == 501
    assert c.get("/v1/targets/wf/profile").status_code == 501
