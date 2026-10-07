from __future__ import annotations

import threading

import pytest

from eiken_grader.core.rate_guard import RateGuard


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_rpm_limit_and_release():
    clock = FakeClock()
    g = RateGuard(rpm=2, rpd=100, clock=clock)
    assert g.try_acquire().ok
    assert g.try_acquire().ok
    denied = g.try_acquire()
    assert not denied.ok and denied.reason == "rpm"
    assert 0 < denied.wait_seconds <= 60
    clock.t += 60
    assert g.try_acquire().ok


def test_rpd_limit():
    clock = FakeClock()
    g = RateGuard(rpm=100, rpd=3, clock=clock)
    for _ in range(3):
        assert g.try_acquire().ok
        clock.t += 61
    denied = g.try_acquire()
    assert not denied.ok and denied.reason == "rpd"
    clock.t += 24 * 3600
    assert g.try_acquire().ok


def test_usage_counts():
    clock = FakeClock()
    g = RateGuard(rpm=5, rpd=5, clock=clock)
    g.try_acquire()
    g.try_acquire()
    assert g.usage() == (2, 2)
    clock.t += 61
    assert g.usage() == (0, 2)


def test_invalid_limits():
    with pytest.raises(ValueError):
        RateGuard(rpm=0, rpd=1)


def test_thread_safety_never_exceeds_limit():
    g = RateGuard(rpm=10, rpd=1000)
    results: list[bool] = []
    lock = threading.Lock()

    def worker():
        for _ in range(20):
            ok = g.try_acquire().ok
            with lock:
                results.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(results) == 10
