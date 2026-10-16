"""스페이스 동작 검증을 위한 정리 보강 (Phase 2 설계 23.3절, FR-217).

검증 시나리오가 시험 스페이스 앞머리로 스페이스를 더 만들 수 있다. 정리는 앞머리가 같은 스페이스를 모두,
"중지면 active로 → owner가 아닌 Crew 빼기 → 지우기" 순서로 지운다.
"""

from __future__ import annotations

import json
import re

import httpx

from a4w_target_workfluence.sandbox import WorkfluenceSpaceSandbox

from .test_sandbox import PREFIX, FakeWorkfluence, make_caller, make_callers, peer_sandbox


class FakeCrew(FakeWorkfluence):
    """Crew·상태를 흉내 낸다. 다른 Crew가 있거나 중지된 스페이스는 owner도 지울 수 없다(403)."""

    def __init__(self):
        super().__init__()
        self.crew: dict[str, dict[str, str]] = {}          # spaceId → {username: role}

    def add_space(self, sid, name, *, status="active", crew=None):
        self.spaces[sid] = {"id": sid, "name": name, "kind": "team", "status": status}
        self.crew[sid] = {"aiagent": "owner", **(crew or {})}

    def __call__(self, req):
        method, path = req.method, req.url.path.removeprefix("/api/v1")
        body = json.loads(req.content) if req.content else {}
        if (method, path) == ("POST", "/spaces"):
            resp = super().__call__(req)
            self.crew[resp.json()["id"]] = {"aiagent": "owner"}
            return resp
        self.calls.append((method, path))
        m = re.fullmatch(r"/spaces/([^/]+)/members", path)
        if m and method == "GET":
            return httpx.Response(200, json=[{"userId": f"u-{u}", "username": u, "displayName": u, "role": r}
                                             for u, r in self.crew.get(m.group(1), {}).items()])
        if m and method == "POST":
            self.crew.setdefault(m.group(1), {})[body["username"]] = body.get("role", "editor")
            return httpx.Response(201, json={"ok": True})
        m = re.fullmatch(r"/spaces/([^/]+)/members/([^/]+)", path)
        if m and method == "DELETE":
            self.crew.get(m.group(1), {}).pop(m.group(2), None)
            return httpx.Response(200, json={"ok": True})
        m = re.fullmatch(r"/spaces/([^/]+)/status", path)
        if m and method == "PATCH":
            self.spaces[m.group(1)]["status"] = body["status"]
            return httpx.Response(200, json=self.spaces[m.group(1)])
        m = re.fullmatch(r"/spaces/([^/]+)", path)
        if m and method == "DELETE":
            sid = m.group(1)
            others = [u for u, r in self.crew.get(sid, {}).items() if r != "owner"]
            if others or self.spaces.get(sid, {}).get("status") != "active":
                return httpx.Response(403, json={"error": {"code": "FORBIDDEN", "message": "x", "requestId": "r"}})
        self.calls.pop()
        return super().__call__(req)


def test_cleanup_sweeps_every_space_with_the_prefix_but_no_other():
    wf = FakeCrew()
    sb = WorkfluenceSpaceSandbox(space_prefix=PREFIX)
    caller = make_caller(wf)
    ctx = sb.prepare(caller, "vf-1")
    wf.add_space("made", PREFIX + "vf-1-new")                    # 시나리오(create_space)가 만든 것
    wf.add_space("human", "사람의 스페이스")
    report = sb.cleanup(caller, ctx)
    assert report.ok
    assert ctx.values["spaceId"] not in wf.spaces and "made" not in wf.spaces
    assert "human" in wf.spaces


def test_suspended_space_is_reactivated_before_delete():
    wf = FakeCrew()
    sb = WorkfluenceSpaceSandbox(space_prefix=PREFIX)
    caller = make_caller(wf)
    ctx = sb.prepare(caller, "vf-1")
    wf.add_space("paused", PREFIX + "vf-1-status", status="suspended")
    assert sb.cleanup(caller, ctx).ok and "paused" not in wf.spaces
    assert wf.calls.index(("PATCH", "/spaces/paused/status")) < wf.calls.index(("DELETE", "/spaces/paused"))


def test_every_non_owner_crew_member_is_removed_before_delete_even_without_peer_settings():
    wf = FakeCrew()
    sb = WorkfluenceSpaceSandbox(space_prefix=PREFIX)
    caller = make_caller(wf)
    ctx = sb.prepare(caller, "vf-1")
    wf.add_space("crewed", PREFIX + "vf-1-crew", crew={"minji": "editor", "jun": "viewer"})
    assert sb.cleanup(caller, ctx).ok and "crewed" not in wf.spaces
    removed = [p for m, p in wf.calls if m == "DELETE" and p.startswith("/spaces/crewed/members/")]
    assert sorted(removed) == ["/spaces/crewed/members/jun", "/spaces/crewed/members/minji"]
    assert ("DELETE", "/spaces/crewed/members/aiagent") not in wf.calls       # owner(자신)는 빼지 않는다


def test_leftovers_from_an_earlier_run_are_swept_the_same_way_on_prepare():
    wf = FakeCrew()
    wf.add_space("old", PREFIX + "vf-0-new", status="suspended", crew={"minji": "editor"})
    WorkfluenceSpaceSandbox(space_prefix=PREFIX).prepare(make_caller(wf), "vf-1")
    assert "old" not in wf.spaces


def test_one_failing_space_is_reported_and_the_rest_are_still_cleaned():
    wf = FakeCrew()
    sb = WorkfluenceSpaceSandbox(space_prefix=PREFIX)
    caller = make_caller(wf)
    ctx = sb.prepare(caller, "vf-1")
    wf.add_space("stuck", PREFIX + "vf-1-a")
    wf.add_space("fine", PREFIX + "vf-1-b")
    wf.fail.add(("DELETE", r"/spaces/stuck"))
    report = sb.cleanup(caller, ctx)
    assert not report.ok and any("stuck" in f for f in report.failures)
    assert "fine" not in wf.spaces and ctx.values["spaceId"] not in wf.spaces


def test_peer_username_is_a_context_key_with_a_value_only_when_a_peer_is_set():
    agent, peer = make_callers(FakeCrewWithTokens())
    with_peer = peer_sandbox(peer)
    assert "peerUsername" in with_peer.context_keys()
    assert with_peer.prepare(agent, "vf-1").values["peerUsername"] == "aiagent-peer"
    alone = WorkfluenceSpaceSandbox(space_prefix=PREFIX)
    assert "peerUsername" in alone.context_keys()
    assert "peerUsername" not in alone.prepare(make_caller(FakeCrew()), "vf-2").values


class FakeCrewWithTokens(FakeCrew):
    """peer 호출(멘션 댓글)도 받는다. 알림은 없어도 된다(이 시험은 peerUsername만 본다)."""

    def __call__(self, req):
        if (req.method, req.url.path.removeprefix("/api/v1")) == ("GET", "/notifications"):
            return httpx.Response(200, json={"items": []})
        return super().__call__(req)
