"""RawCaller: 시험 환경 준비·정리처럼 tool이 아닌 호출도 같은 클라이언트·규약으로."""

from __future__ import annotations

import json

import httpx
import pytest

from a4w_tools.call_policy import ConfigCallPolicy
from a4w_tools.sandbox import CallFailed, RawCaller
from a4w_tools.target_http import TargetHttpClient


class Clock:
    now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.now += s


def caller(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    http = TargetHttpClient(base_url="https://wf.test/api/v1", token="t", client=client)
    policy = ConfigCallPolicy(read_per_minute=10, write_per_minute=10, max_attempts=1, max_retry_wait_seconds=1, clock=Clock())
    return RawCaller(http, policy)


def test_call_returns_status_and_json():
    seen = []

    def h(req):
        seen.append(req)
        return httpx.Response(201, json={"id": "s1"})

    r = caller(h).call("POST", "/spaces", json={"name": "x"})
    assert r.status == 201
    assert r.data == {"id": "s1"}
    assert json.loads(seen[0].content) == {"name": "x"}


def test_expect_raises_on_unexpected_status():
    c = caller(lambda req: httpx.Response(403, json={"error": {"code": "FORBIDDEN", "message": "no", "requestId": "r"}}))
    with pytest.raises(CallFailed) as e:
        c.expect("DELETE", "/spaces/1", ok=(200,))
    assert e.value.status == 403
    assert "FORBIDDEN" in str(e.value)


def test_transport_failure_raises():
    def h(req):
        raise httpx.ConnectError("down")

    with pytest.raises(CallFailed):
        caller(h).call("GET", "/spaces")
