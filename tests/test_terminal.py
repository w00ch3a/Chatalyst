from __future__ import annotations

import sys

import pytest

from chatalyst.core.terminal import TerminalOutputLimitError, TerminalRunner


@pytest.mark.asyncio
async def test_terminal_runner_kills_command_when_output_limit_is_exceeded(tmp_path):
    runner = TerminalRunner(cwd=tmp_path, timeout_seconds=5, output_limit=1024)

    with pytest.raises(TerminalOutputLimitError, match="exceeded 1024 bytes"):
        await runner.run_argv(
            command="python noisy",
            argv=(
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('x' * 2048); sys.stdout.flush()",
            ),
        )


@pytest.mark.asyncio
async def test_terminal_runner_returns_output_under_limit(tmp_path):
    runner = TerminalRunner(cwd=tmp_path, timeout_seconds=5, output_limit=1024)

    result = await runner.run_argv(
        command="python quiet",
        argv=(sys.executable, "-c", "print('hello')"),
    )

    assert result.exit_code == 0
    assert result.stdout == "hello\n"
