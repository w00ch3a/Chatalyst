from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from pathlib import Path


class TerminalTimeoutError(TimeoutError):
    pass


class TerminalOutputLimitError(RuntimeError):
    pass


@dataclass(frozen=True)
class TerminalResult:
    command: str
    argv: tuple[str, ...]
    cwd: Path
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    def as_markdown(self) -> str:
        return (
            f"## Terminal\n\n"
            f"```bash\n{self.command}\n```\n\n"
            f"Exit code: `{self.exit_code}`\n\n"
            f"### stdout\n\n```text\n{self.stdout or ''}\n```\n\n"
            f"### stderr\n\n```text\n{self.stderr or ''}\n```"
        )


class TerminalRunner:
    """Controlled local terminal bridge for TUI commands.

    Commands are parsed with `shlex` and executed without a shell. Shell pipes,
    redirects, aliases, and variable expansion are intentionally not interpreted.
    """

    def __init__(
        self,
        *,
        cwd: Path,
        timeout_seconds: float = 30.0,
        output_limit: int = 24_000,
    ) -> None:
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds
        self.output_limit = output_limit

    def parse(self, command: str) -> tuple[str, ...]:
        stripped = command.strip()
        if not stripped:
            raise ValueError("Cannot run an empty terminal command.")
        return tuple(shlex.split(stripped))

    async def run(self, command: str) -> TerminalResult:
        argv = self.parse(command)
        return await self.run_argv(command=command, argv=argv)

    async def run_argv(self, *, command: str, argv: tuple[str, ...]) -> TerminalResult:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=self.cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                self._collect_output(process),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as exc:
            await self._kill_process(process)
            raise TerminalTimeoutError(
                f"Terminal command timed out after {self.timeout_seconds:.1f}s: {command}"
            ) from exc
        except TerminalOutputLimitError as exc:
            await self._kill_process(process)
            raise TerminalOutputLimitError(
                f"Terminal command exceeded {self.output_limit} bytes of output: {command}"
            ) from exc
        return TerminalResult(
            command=command,
            argv=argv,
            cwd=self.cwd,
            exit_code=process.returncode or 0,
            stdout=self._decode(stdout_bytes),
            stderr=self._decode(stderr_bytes),
        )

    async def _kill_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.wait()

    async def _collect_output(
        self,
        process: asyncio.subprocess.Process,
    ) -> tuple[bytes, bytes]:
        stdout = bytearray()
        stderr = bytearray()

        async def read_stream(
            stream: asyncio.StreamReader | None,
            target: bytearray,
        ) -> None:
            if stream is None:
                return
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    return
                target.extend(chunk)
                if len(stdout) + len(stderr) > self.output_limit:
                    raise TerminalOutputLimitError

        stdout_task = asyncio.create_task(read_stream(process.stdout, stdout))
        stderr_task = asyncio.create_task(read_stream(process.stderr, stderr))
        wait_task = asyncio.create_task(process.wait())
        tasks = {stdout_task, stderr_task, wait_task}
        try:
            await asyncio.gather(*tasks)
        except Exception:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return bytes(stdout), bytes(stderr)

    def _decode(self, value: bytes) -> str:
        text = value.decode("utf-8", errors="replace")
        if len(text) <= self.output_limit:
            return text
        omitted = len(text) - self.output_limit
        return text[: self.output_limit] + f"\n...[truncated {omitted} chars]"
