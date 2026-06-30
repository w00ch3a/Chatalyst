from __future__ import annotations

from chatalyst.core import processes


def test_live_chatalyst_processes_reports_workspace_duplicates(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"

    class Result:
        stdout = "\n".join(
            [
                f"101 /bin/sh /usr/bin/xvfb-run -a chatalyst-mcp --workspace {workspace}",
                f"102 /venv/bin/python chatalyst-mcp --workspace {workspace}",
                "103 chatalyst-mcp --workspace /other",
                "104 python unrelated",
                f"105 chatalyst-mcp --workspace={workspace}",
                "106 chatalyst-mcp",
            ]
        )

    monkeypatch.setattr(processes.subprocess, "run", lambda *_args, **_kwargs: Result())

    found = processes.live_chatalyst_processes(workspace)

    assert [process.pid for process in found] == [101, 102, 105]


def test_kill_workspace_mcp_processes_kills_all_workspace_mcp(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    killed: list[int] = []

    monkeypatch.setattr(
        processes,
        "live_chatalyst_processes",
        lambda _workspace: [
            processes.ChatalystProcess(pid=101, command="chatalyst-mcp"),
            processes.ChatalystProcess(pid=102, command="chatalyst-mcp"),
        ],
    )
    monkeypatch.setattr(processes.os, "kill", lambda pid, _signal: killed.append(pid))

    result = processes.kill_workspace_mcp_processes(workspace)

    assert result == [101, 102]
    assert killed == [101, 102]
