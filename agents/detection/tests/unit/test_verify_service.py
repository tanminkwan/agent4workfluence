"""검증 접수·멱등·잠금·기록, 타겟 정보 (Phase 2 설계 11.3·11.5절)."""

from __future__ import annotations

import pytest

from a4w_core.errors import (
    ScopeUnknown, TargetNotFound, UnsupportedSandbox, VerificationNotFound, VerifyInProgress, VerifyRequestInvalid,
)
from a4w_core.interfaces import ScopeProvider
from a4w_core.locking import FileTargetLock
from a4w_detection.profile import TargetProfileService
from a4w_detection.verify.runner import VerificationRunner
from a4w_detection.verify.service import VerificationService, VerifyRuntime
from a4w_detection.verify.store import SqliteVerificationStore

from ..fakes import FixedClock
from ..verify_fakes import SPEC_OPS, FakeSandbox, add_comment, list_users, make_runtime_parts, read_page


class Scopes(ScopeProvider):
    def __init__(self, value=frozenset({"read", "write", "admin"}), fail=False):
        self.value, self.fail = value, fail

    def scopes(self, token):
        if self.fail:
            raise ScopeUnknown("x")
        return self.value


class ManualStarter:
    """작업을 바로 돌리지 않고 모아 두었다가 시험이 돌린다."""

    def __init__(self):
        self.jobs = []

    def __call__(self, job):
        self.jobs.append(job)

    def run_all(self):
        while self.jobs:
            self.jobs.pop(0)()


def make(tmp_path, sandbox=None, scopes=None, with_runtime=True):
    _, executor, caller = make_runtime_parts()
    sandbox = sandbox or FakeSandbox()
    rt = VerifyRuntime(
        runner=VerificationRunner(executor=executor, caller=caller, sandbox=sandbox),
        scope_provider=scopes or Scopes(), token="SECRET-TOKEN-123", sandbox_name="fake",
        context_keys=sandbox.context_keys(), deny_scopes=frozenset({"admin"}),
    )
    starter = ManualStarter()
    svc = VerificationService(
        known_targets={"wf", "other"}, runtimes={"wf": rt} if with_runtime else {},
        store=SqliteVerificationStore(tmp_path / "verify.db"),
        lock=FileTargetLock(tmp_path / "locks", busy=lambda k: VerifyInProgress(k)),
        spec_operations=lambda tid: SPEC_OPS, clock=FixedClock(), max_tools=10, keep_days=30, starter=starter,
    )
    profile = TargetProfileService(known_targets={"wf", "other"}, runtimes=svc.runtimes)
    return svc, starter, profile


def payload(vid="vf-1", tools=None, scenarios=None):
    return {"targetId": "wf", "verificationId": vid, "tools": tools or [read_page()], "references": [],
            "scenarios": scenarios or {}}


def test_submit_runs_and_records(tmp_path):
    svc, starter, _ = make(tmp_path)
    rec, created = svc.submit(payload())
    assert created and rec["status"] == "running"
    starter.run_all()
    done = svc.get("wf", "vf-1")
    assert done["status"] == "done"
    assert done["results"][0]["result"] == "passed"
    assert done["finishedAt"]


def test_same_id_is_idempotent(tmp_path):
    svc, starter, _ = make(tmp_path)
    svc.submit(payload())
    starter.run_all()
    rec, created = svc.submit(payload())
    assert not created and rec["status"] == "done"
    assert starter.jobs == []


def test_second_verification_while_running_is_409(tmp_path):
    svc, starter, _ = make(tmp_path)
    svc.submit(payload("vf-1"))
    with pytest.raises(VerifyInProgress):
        svc.submit(payload("vf-2"))
    starter.run_all()
    svc.submit(payload("vf-2"))  # 끝난 뒤에는 된다


def test_invalid_request_is_rejected_with_details(tmp_path):
    svc, starter, _ = make(tmp_path)
    with pytest.raises(VerifyRequestInvalid) as e:
        svc.submit(payload(tools=[list_users()]))
    assert e.value.details[0]["reason"] == "DENIED_SCOPE"
    assert starter.jobs == []


def test_unknown_target_and_missing_runtime(tmp_path):
    svc, _, _ = make(tmp_path)
    with pytest.raises(TargetNotFound):
        svc.submit({**payload(), "targetId": "nope"})
    with pytest.raises(UnsupportedSandbox):
        svc.submit({**payload(), "targetId": "other"})


def test_get_unknown_or_other_target(tmp_path):
    svc, starter, _ = make(tmp_path)
    with pytest.raises(VerificationNotFound):
        svc.get("wf", "vf-x")
    svc.submit(payload())
    with pytest.raises(VerificationNotFound):
        svc.get("other", "vf-1")


def test_prepare_failure_marks_failed_and_releases_lock(tmp_path):
    svc, starter, _ = make(tmp_path, sandbox=FakeSandbox(fail_prepare=True))
    svc.submit(payload())
    starter.run_all()
    rec = svc.get("wf", "vf-1")
    assert rec["status"] == "failed" and rec["error"]["code"] == "SANDBOX_UNAVAILABLE"
    svc.submit(payload("vf-2"))  # 잠금이 풀렸다


def test_unexpected_crash_marks_failed(tmp_path, monkeypatch):
    svc, starter, _ = make(tmp_path)
    monkeypatch.setattr(svc.runtimes["wf"].runner, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    svc.submit(payload())
    starter.run_all()
    assert svc.get("wf", "vf-1")["error"]["code"] == "INTERNAL_ERROR"


def test_recover_marks_running_as_interrupted(tmp_path):
    svc, _, _ = make(tmp_path)
    svc.submit(payload())  # 돌리지 않음 = 프로세스가 죽은 것처럼
    svc2, _, _ = make(tmp_path)
    svc2.recover()
    assert svc2.get("wf", "vf-1")["error"]["code"] == "INTERRUPTED"


def test_record_keeps_names_not_definitions(tmp_path):
    svc, starter, _ = make(tmp_path)
    svc.submit(payload(tools=[read_page(), add_comment()]))
    starter.run_all()
    rec = svc.get("wf", "vf-1")
    fp = rec["request"].pop("fingerprint")
    assert len(fp) == 64
    assert rec["request"] == {"tools": [{"name": "read_page", "revision": "r-" + "0" * 12},
                                        {"name": "add_comment", "revision": "r-" + "0" * 12}],
                              "references": [], "scenarios": []}


def test_profile(tmp_path):
    _, _, profile = make(tmp_path)
    p = profile.profile("wf")
    assert p["tokenScopes"] == ["admin", "read", "write"]
    assert p["verification"]["available"] and "pageId" in p["verification"]["contextKeys"]
    assert "SECRET-TOKEN-123" not in str(p)
    assert profile.profile("other") == {"targetId": "other", "tokenScopes": [],
                                        "verification": {"available": False, "sandbox": None, "contextKeys": []}}
    with pytest.raises(TargetNotFound):
        profile.profile("nope")


def test_profile_scope_unknown(tmp_path):
    _, _, profile = make(tmp_path, scopes=Scopes(fail=True))
    with pytest.raises(ScopeUnknown):
        profile.profile("wf")


def test_same_id_with_different_request_is_rejected(tmp_path):
    """같은 verificationId로 다른 내용을 보내면 옛 결과를 돌려주지 않고 거절한다 (조용히 낡은 결과를 쓰지 않게)."""
    svc, starter, _ = make(tmp_path)
    svc.submit(payload())
    starter.run_all()
    changed = read_page()
    changed["title"] = "다른 정의"
    with pytest.raises(VerifyRequestInvalid) as e:
        svc.submit(payload(tools=[dict(changed, revision="r-" + "1" * 12)]))
    assert "다른 요청" in e.value.message
    rec, created = svc.submit(payload())  # 같은 내용이면 그대로 앞선 기록
    assert not created


def test_same_id_with_different_scenarios_is_rejected(tmp_path):
    svc, starter, _ = make(tmp_path)
    svc.submit(payload())
    starter.run_all()
    with pytest.raises(VerifyRequestInvalid):
        svc.submit(payload(scenarios={"read_page": {"call": {"args": {"pageId": "${sandbox.pageId}"}}}}))
