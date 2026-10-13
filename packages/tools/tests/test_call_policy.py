"""호출 규약: 한도 대기, 429·5xx·시간 초과 처리."""

from __future__ import annotations

import httpx
import pytest

from a4w_tools.call_policy import ConfigCallPolicy


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def resp(status, headers=None, body=b"{}"):
    return httpx.Response(status, headers=headers or {}, content=body, request=httpx.Request("GET", "http://x/"))


def seq(*items):
    it = iter(items)

    def send():
        item = next(it)
        if isinstance(item, Exception):
            raise item
        return item

    return send


@pytest.fixture
def clock():
    return FakeClock()


def policy(clock, **over):
    kw = dict(read_per_minute=120, write_per_minute=30, max_attempts=3, max_retry_wait_seconds=60, clock=clock)
    kw.update(over)
    return ConfigCallPolicy(**kw)


def test_success_first_try(clock):
    out = policy(clock).run(seq(resp(200)), write=False)
    assert out.response.status_code == 200
    assert out.attempts == 1
    assert out.error is None


def test_429_waits_retry_after_then_succeeds(clock):
    out = policy(clock).run(seq(resp(429, {"Retry-After": "2"}), resp(200)), write=True)
    assert out.response.status_code == 200
    assert out.attempts == 2
    assert clock.slept == [2.0]


def test_429_wait_too_long_gives_rate_limited(clock):
    out = policy(clock).run(seq(resp(429, {"Retry-After": "120"})), write=False)
    assert out.error.code == "RATE_LIMITED"
    assert out.error.retryable
    assert clock.slept == []


def test_5xx_read_is_retried_until_max(clock):
    out = policy(clock).run(seq(resp(503), resp(502), resp(500)), write=False)
    assert out.attempts == 3
    assert out.response.status_code == 500


def test_5xx_read_recovers(clock):
    out = policy(clock).run(seq(resp(503), resp(200)), write=False)
    assert out.response.status_code == 200


def test_5xx_write_is_not_retried(clock):
    out = policy(clock).run(seq(resp(503), resp(200)), write=True)
    assert out.attempts == 1
    assert out.response.status_code == 503


def test_4xx_is_not_retried(clock):
    out = policy(clock).run(seq(resp(409), resp(200)), write=True)
    assert out.attempts == 1
    assert out.response.status_code == 409


def test_write_timeout_is_outcome_unknown(clock):
    out = policy(clock).run(seq(httpx.ReadTimeout("t")), write=True)
    assert out.error.code == "OUTCOME_UNKNOWN"
    assert out.error.hint


def test_read_timeout_is_retried_then_timeout(clock):
    out = policy(clock).run(seq(httpx.ReadTimeout("t"), httpx.ReadTimeout("t"), httpx.ReadTimeout("t")), write=False)
    assert out.error.code == "TIMEOUT"
    assert out.attempts == 3


def test_connect_error_on_write_is_retried(clock):
    out = policy(clock).run(seq(httpx.ConnectError("c"), resp(201)), write=True)
    assert out.response.status_code == 201


def test_connect_error_exhausted_is_unavailable(clock):
    out = policy(clock).run(seq(*[httpx.ConnectError("c")] * 3), write=True)
    assert out.error.code == "TARGET_UNAVAILABLE"
    assert out.error.retryable


def test_rate_window_waits_when_full(clock):
    p = policy(clock, write_per_minute=2)
    p.run(seq(resp(200)), write=True)
    clock.now += 10
    p.run(seq(resp(200)), write=True)
    p.run(seq(resp(200)), write=True)  # 세 번째는 첫 호출로부터 60초가 될 때까지 기다린다
    assert clock.slept == [50.0]


def test_read_and_write_windows_are_separate(clock):
    p = policy(clock, write_per_minute=1, read_per_minute=1)
    p.run(seq(resp(200)), write=True)
    p.run(seq(resp(200)), write=False)
    assert clock.slept == []
