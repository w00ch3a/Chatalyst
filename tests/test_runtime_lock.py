from __future__ import annotations

import asyncio
import fcntl
import os
import threading
import time

import pytest

from chatalyst.core.runtime import RuntimeLock, RuntimeLockError


def test_runtime_lock_waits_for_busy_owner(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    first = RuntimeLock(lock_path)
    first.acquire()

    def release_later() -> None:
        time.sleep(0.1)
        first.release()

    releaser = threading.Thread(target=release_later)
    releaser.start()
    second = RuntimeLock(lock_path, timeout_seconds=1, poll_interval_seconds=0.02)
    try:
        second.acquire()
        assert lock_path.exists()
    finally:
        second.release()
        releaser.join(timeout=1)


def test_runtime_lock_times_out_when_owner_stays_busy(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    first = RuntimeLock(lock_path)
    first.acquire()
    try:
        second = RuntimeLock(lock_path, timeout_seconds=0.05, poll_interval_seconds=0.01)
        with pytest.raises(RuntimeLockError, match="browser lane is busy"):
            second.acquire()
    finally:
        first.release()


def test_runtime_lock_does_not_unlink_empty_locked_file(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        contender = RuntimeLock(lock_path, timeout_seconds=0.05, poll_interval_seconds=0.01)
        with pytest.raises(RuntimeLockError, match="pid unknown"):
            contender.acquire()
        assert lock_path.exists()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@pytest.mark.asyncio
async def test_runtime_lock_async_wait_does_not_block_event_loop(tmp_path, monkeypatch):
    lock_path = tmp_path / "runtime.lock"
    first = RuntimeLock(lock_path)
    first.acquire()
    second = RuntimeLock(lock_path, timeout_seconds=0.1, poll_interval_seconds=0.02)
    ticks = 0

    async def fail_to_thread(*_args, **_kwargs):
        raise AssertionError("acquire_async must not block through asyncio.to_thread")

    monkeypatch.setattr(asyncio, "to_thread", fail_to_thread)

    async def ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.01)

    ticker_task = asyncio.create_task(ticker())
    try:
        with pytest.raises(RuntimeLockError):
            await second.acquire_async()
        assert ticks > 1
    finally:
        ticker_task.cancel()
        first.release()
