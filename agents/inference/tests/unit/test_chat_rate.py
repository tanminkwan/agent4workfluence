"""RateLimiter: 보내기 속도 제한 (Phase 5 설계 5.2절 4번, FR-C27, AC-C6).

서버 한도는 사용자 단위 분당 60이다. 토큰 버킷은 창 경계에서 한도의 두 배까지 보낼 수 있어서 맞지 않고,
"어느 60초 구간에도 N건 이하"를 지키는 미끄러지는 창으로 센다. 가짜 시계·sleep으로 결정적으로 본다.
"""

from __future__ import annotations

import asyncio

import pytest

from a4w_inference.channels.chat.rate import RateLimiter

pytestmark = pytest.mark.anyio


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds
        await asyncio.sleep(0)


def make(n=3, window=60.0):
    c = Clock()
    return RateLimiter(max_per_window=n, window_seconds=window, clock=c.now, sleep=c.sleep), c


async def test_R1_the_first_n_acquires_do_not_wait():
    lim, c = make(3)
    for _ in range(3):
        await lim.acquire()
    assert c.sleeps == []


async def test_R2_the_next_acquire_waits_until_the_oldest_one_leaves_the_window():
    lim, c = make(3, 60.0)
    await lim.acquire()                       # t=1000
    c.t += 10
    await lim.acquire()                       # t=1010
    await lim.acquire()                       # t=1010
    c.t += 5                                  # t=1015
    await lim.acquire()                       # 가장 오래된 것(1000)이 1060에 창을 벗어난다 → 45초 기다린다
    assert c.sleeps == [45.0] and c.t == 1060.0


async def test_R3_no_rolling_window_ever_holds_more_than_n_acquires():
    lim, c = make(5, 60.0)
    stamps = []
    for _ in range(60):
        await lim.acquire()
        stamps.append(c.t)
    for t in stamps:
        assert sum(1 for s in stamps if t - 60.0 < s <= t) <= 5


async def test_R4_concurrent_acquirers_are_served_in_order_and_respect_the_limit():
    lim, c = make(2, 60.0)
    order, stamps = [], []

    async def worker(i):
        await lim.acquire()
        order.append(i)
        stamps.append(c.t)

    await asyncio.gather(*(worker(i) for i in range(7)))
    assert order == list(range(7))
    for t in stamps:
        assert sum(1 for s in stamps if t - 60.0 < s <= t) <= 2


async def test_R5_the_window_length_is_configurable():
    lim, c = make(1, 10.0)
    await lim.acquire()
    await lim.acquire()
    assert c.sleeps == [10.0]


async def test_R6_a_cancelled_waiter_does_not_use_up_a_slot():
    lim, c = make(1, 60.0)
    await lim.acquire()
    gate = asyncio.Event()

    async def hang(seconds):
        await gate.wait()

    lim2 = RateLimiter(max_per_window=1, window_seconds=60.0, clock=c.now, sleep=hang)
    await lim2.acquire()
    task = asyncio.create_task(lim2.acquire())
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert lim2.in_window() == 1


@pytest.mark.parametrize("n,window", [(0, 60.0), (-1, 60.0), (1, 0.0), (1, -5.0)])
def test_R7_invalid_settings_are_rejected(n, window):
    with pytest.raises(ValueError):
        RateLimiter(max_per_window=n, window_seconds=window)
