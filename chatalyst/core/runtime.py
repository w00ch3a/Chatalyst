from __future__ import annotations

import asyncio
import fcntl
import os
import time
from dataclasses import dataclass
from pathlib import Path


class RuntimeLockError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeLockStatus:
    path: Path
    exists: bool
    owner_pid: int | None
    owner_alive: bool | None
    locked: bool


class RuntimeLock:
    """Exclusive workspace runtime lock for one browser/profile owner."""

    def __init__(
        self,
        path: Path,
        *,
        timeout_seconds: float = 0,
        poll_interval_seconds: float = 0.25,
    ) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self._owned = False
        self._fd: int | None = None

    def acquire(self) -> RuntimeLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                if self.timeout_seconds > 0 and time.monotonic() < deadline:
                    time.sleep(self.poll_interval_seconds)
                    continue
                owner = self._read_owner(fd) or "unknown"
                os.close(fd)
                raise RuntimeLockError(
                    "Chatalyst browser lane is busy "
                    f"(pid {owner}). Timed out waiting for exclusive access."
                ) from exc
            self._claim_fd(fd)
            return self

    async def acquire_async(self) -> RuntimeLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                if self.timeout_seconds > 0 and time.monotonic() < deadline:
                    await asyncio.sleep(self.poll_interval_seconds)
                    continue
                owner = self._read_owner(fd) or "unknown"
                os.close(fd)
                raise RuntimeLockError(
                    "Chatalyst browser lane is busy "
                    f"(pid {owner}). Timed out waiting for exclusive access."
                ) from exc
            self._claim_fd(fd)
            return self

    def _claim_fd(self, fd: int) -> None:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, f"{os.getpid()}\n".encode())
        os.fsync(fd)
        self._fd = fd
        self._owned = True

    def release(self) -> None:
        if self._owned and self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
            self._owned = False

    def __enter__(self) -> RuntimeLock:
        return self.acquire()

    def __exit__(self, *_exc: object) -> None:
        self.release()

    async def __aenter__(self) -> RuntimeLock:
        return await self.acquire_async()

    async def __aexit__(self, *_exc: object) -> None:
        self.release()

    def _read_owner(self, fd: int) -> str:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            return os.read(fd, 64).decode("utf-8", errors="replace").strip()
        except OSError:
            return ""

    @classmethod
    def status(cls, path: Path) -> RuntimeLockStatus:
        if not path.exists():
            return RuntimeLockStatus(
                path=path,
                exists=False,
                owner_pid=None,
                owner_alive=None,
                locked=False,
            )
        owner_pid = cls._read_owner_from_path(path)
        owner_alive = cls._pid_alive(owner_pid) if owner_pid is not None else None
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        locked = False
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
            except BlockingIOError:
                locked = True
        finally:
            os.close(fd)
        return RuntimeLockStatus(
            path=path,
            exists=True,
            owner_pid=owner_pid,
            owner_alive=owner_alive,
            locked=locked,
        )

    @staticmethod
    def _read_owner_from_path(path: Path) -> int | None:
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not raw.isdigit():
            return None
        return int(raw)

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @classmethod
    def clean_stale(cls, path: Path) -> bool:
        status = cls.status(path)
        if status.exists and not status.locked and status.owner_alive is False:
            path.unlink(missing_ok=True)
            return True
        return False
