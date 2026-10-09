import multiprocessing
import time

import pytest

from foundry_imagegen.ratelimit import RateLimiter, RateLimitExceeded


class FakeClock:
    def __init__(self, start=1_000_000.0):
        self.now = start
        self.sleeps = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make(tmp_path, clock, rpm=5):
    return RateLimiter(tmp_path, "endpoint|dep", rpm, clock=clock, sleep=clock.sleep)


async def test_allows_rpm_then_waits(tmp_path):
    clock = FakeClock()
    limiter = make(tmp_path, clock)
    for _ in range(5):
        await limiter.acquire(clock.now + 1)
    assert clock.sleeps == []
    waits = []

    async def on_wait(seconds, reason):
        waits.append((round(seconds), reason))

    await limiter.acquire(clock.now + 120, on_wait)
    assert sum(clock.sleeps) == pytest.approx(60.5)
    assert waits[0] == (60, "5 requests per minute")


async def test_raises_when_wait_exceeds_deadline(tmp_path):
    clock = FakeClock()
    limiter = make(tmp_path, clock, rpm=1)
    await limiter.acquire(clock.now + 1)
    with pytest.raises(RateLimitExceeded) as info:
        await limiter.acquire(clock.now + 10)
    assert info.value.retry_at == pytest.approx(clock.now + 60.5)


async def test_cooldown_shared_between_instances(tmp_path):
    clock = FakeClock()
    a = make(tmp_path, clock)
    b = make(tmp_path, clock)
    a.set_cooldown(20)
    with pytest.raises(RateLimitExceeded, match="back off"):
        await b.acquire(clock.now + 5)
    await b.acquire(clock.now + 30)
    assert sum(clock.sleeps) == pytest.approx(20)


async def test_observed_limit_lowers_rpm(tmp_path):
    clock = FakeClock()
    limiter = make(tmp_path, clock)
    limiter.observe_limit(2)
    await limiter.acquire(clock.now + 1)
    await limiter.acquire(clock.now + 1)
    with pytest.raises(RateLimitExceeded):
        await limiter.acquire(clock.now + 1)
    assert limiter.status()["rpm_limit"] == 2
    limiter.observe_limit(50)  # a higher service limit never raises the configured RPM
    assert limiter.status()["rpm_limit"] == 5


def _worker(state_dir, results):
    import anyio

    limiter = RateLimiter(state_dir, "endpoint|dep", 3)

    async def go():
        try:
            await limiter.acquire(time.time() + 0.5)
            results.append("ok")
        except RateLimitExceeded:
            results.append("limited")

    anyio.run(go)


def test_cross_process_contention(tmp_path):
    with multiprocessing.Manager() as manager:
        results = manager.list()
        procs = [multiprocessing.Process(target=_worker, args=(tmp_path, results)) for _ in range(6)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(20)
        assert sorted(results) == ["limited"] * 3 + ["ok"] * 3
