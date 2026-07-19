"""Unit tests for ClockSyncer drift/cooldown policy."""

from __future__ import annotations

import os
import subprocess

import pytest

from aaos_bridge.clock_sync import ClockSyncer


class _FakeClock:
    def __init__(self, wall: float = 1_700_000_000.0, mono: float = 0.0) -> None:
        self.wall = wall
        self.mono = mono

    def time(self) -> float:
        return self.wall

    def monotonic(self) -> float:
        return self.mono


class _FakeRunner:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, cmd, **_kwargs):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self.returncode, "", self.stderr)


def _make(tmp_path, **kw):
    # Real path that os.access reports executable, so `enabled` is True.
    helper = tmp_path / "aaos-set-time"
    helper.write_text("#!/bin/sh\nexit 0\n")
    helper.chmod(0o755)
    clock = _FakeClock()
    runner = _FakeRunner()
    syncer = ClockSyncer(
        helper_path=str(helper),
        runner=runner,
        time_fn=clock.time,
        monotonic_fn=clock.monotonic,
        **kw,
    )
    return syncer, runner, clock


def test_disabled_when_helper_missing(tmp_path):
    s = ClockSyncer(helper_path=str(tmp_path / "nope"))
    assert s.enabled is False
    # consider() must be a no-op even with a wildly different ts.
    assert s.consider(9_999_999_999_000) is False


def test_skips_when_within_threshold(tmp_path):
    syncer, runner, clock = _make(tmp_path, drift_threshold_s=5.0)
    # frame_ts within 2s of wall — under the 5s threshold.
    assert syncer.consider(int((clock.wall + 2.0) * 1000)) is False
    assert runner.calls == []


def test_syncs_when_drift_exceeds_threshold(tmp_path):
    syncer, runner, clock = _make(tmp_path, drift_threshold_s=5.0)
    target = clock.wall + 32_400.0  # 9h ahead, like the real Pi case
    assert syncer.consider(int(target * 1000)) is True
    assert len(runner.calls) == 1
    cmd = runner.calls[0]
    assert cmd[:3] == ["sudo", "-n", str(syncer._helper)]
    assert cmd[3] == str(int(target))


def test_cooldown_blocks_rapid_resync(tmp_path):
    syncer, runner, clock = _make(
        tmp_path, drift_threshold_s=5.0, min_resync_interval_s=300.0
    )
    target_ms = int((clock.wall + 32_400.0) * 1000)
    assert syncer.consider(target_ms) is True
    # 60s of monotonic time later, drift still huge — must NOT resync yet.
    clock.mono += 60.0
    assert syncer.consider(target_ms) is False
    assert len(runner.calls) == 1
    # After the cooldown elapses, it should fire again.
    clock.mono += 300.0
    assert syncer.consider(target_ms) is True
    assert len(runner.calls) == 2


def test_ignores_bogus_pre_2023_timestamps(tmp_path):
    syncer, runner, _ = _make(tmp_path)
    # ts=1 is what the existing aaos_bridge tests use as a placeholder.
    assert syncer.consider(1) is False
    assert syncer.consider(0) is False
    assert syncer.consider(None) is False
    assert syncer.consider("not-a-number") is False
    assert runner.calls == []


def test_helper_failure_logged_but_not_raised(tmp_path):
    syncer, _runner, clock = _make(tmp_path)
    bad_runner = _FakeRunner(returncode=3, stderr="invalid: not numeric")
    syncer._run = bad_runner
    target = clock.wall + 32_400.0
    # Should not raise, returns True (we DID attempt), cooldown still set.
    assert syncer.consider(int(target * 1000)) is True
    assert syncer.consider(int(target * 1000)) is False  # cooldown
