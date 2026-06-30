from __future__ import annotations

import os
import shlex
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ChatalystProcess:
    pid: int
    command: str


def _workspace_from_argv(argv: list[str]) -> Path | None:
    for index, arg in enumerate(argv):
        if arg == "--workspace" and index + 1 < len(argv):
            return Path(argv[index + 1]).expanduser().resolve()
        if arg.startswith("--workspace="):
            return Path(arg.split("=", 1)[1]).expanduser().resolve()
    return None


def live_chatalyst_processes(workspace: Path) -> list[ChatalystProcess]:
    workspace_path = workspace.expanduser().resolve()
    rows = subprocess.run(
        ["ps", "-eo", "pid=,args="],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    current_pid = os.getpid()
    processes: list[ChatalystProcess] = []
    for row in rows:
        row = row.strip()
        if not row:
            continue
        pid_text, _, command = row.partition(" ")
        if not pid_text.isdigit():
            continue
        pid = int(pid_text)
        if pid == current_pid or "chatalyst-mcp" not in command:
            continue
        try:
            argv = shlex.split(command)
        except ValueError:
            argv = command.split()
        if _workspace_from_argv(argv) != workspace_path:
            continue
        processes.append(ChatalystProcess(pid=pid, command=command))
    return processes


def kill_extra_chatalyst_processes(workspace: Path) -> list[int]:
    processes = sorted(live_chatalyst_processes(workspace), key=lambda item: item.pid)
    victims = processes[1:]
    for process in victims:
        try:
            os.kill(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    return [process.pid for process in victims]


def kill_workspace_mcp_processes(workspace: Path) -> list[int]:
    victims = sorted(live_chatalyst_processes(workspace), key=lambda item: item.pid)
    for process in victims:
        try:
            os.kill(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    return [process.pid for process in victims]
