from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from chatalyst.core.cache import ChatCache
from chatalyst.core.models import MessageRole, Snippet, SnippetRunMode, SnippetStatus
from chatalyst.core.terminal import TerminalResult, TerminalRunner


class SnippetService:
    def __init__(self, *, cache: ChatCache, snippets_dir: Path) -> None:
        self.cache = cache
        self.snippets_dir = snippets_dir

    def stage_last_code_block(self, conversation_id: str) -> Snippet | None:
        messages = [
            message
            for message in self.cache.list_messages(conversation_id)
            if message.role is MessageRole.ASSISTANT and message.code_blocks
        ]
        if not messages:
            return None
        message = messages[-1]
        block = message.code_blocks[-1]
        return self.stage_text(
            conversation_id=conversation_id,
            message_id=message.id,
            body=block.code,
            language=block.language,
        )

    def stage_text(
        self,
        *,
        conversation_id: str | None,
        message_id: str | None,
        body: str,
        language: str | None = None,
    ) -> Snippet:
        run_mode = self._run_mode(language)
        snippet = Snippet(
            conversation_id=conversation_id,
            message_id=message_id,
            body=body.rstrip() + "\n",
            language=language.lower() if language else None,
            run_mode=run_mode,
            status=SnippetStatus.STAGED,
        )
        snippet = self.cache.create_snippet(snippet)
        saved = self.save(snippet)
        return saved

    def save(self, snippet: Snippet) -> Snippet:
        self.snippets_dir.mkdir(parents=True, exist_ok=True)
        self.snippets_dir.chmod(0o700)
        extension = self._extension(snippet)
        path = self.snippets_dir / f"{self._stamp()}-snippet-{snippet.id or 'new'}.{extension}"
        path.write_text(snippet.body, encoding="utf-8")
        path.chmod(0o700 if snippet.run_mode is SnippetRunMode.SHELL_SCRIPT else 0o600)
        saved = snippet.model_copy(
            update={
                "path": str(path),
                "status": SnippetStatus.SAVED,
            }
        )
        self.cache.update_snippet(saved)
        return saved

    async def run(
        self,
        snippet: Snippet,
        runner: TerminalRunner,
        *,
        python_executable: str = "python3",
    ) -> TerminalResult:
        if snippet.path is None:
            snippet = self.save(snippet)
        if snippet.run_mode is SnippetRunMode.SHELL_SCRIPT:
            argv = ("bash", snippet.path)
        elif snippet.run_mode is SnippetRunMode.PYTHON_SCRIPT:
            argv = (python_executable, snippet.path)
        else:
            raise ValueError(f"Snippet language is not runnable: {snippet.language or 'unknown'}")
        command = " ".join(argv)
        try:
            result = await runner.run_argv(command=command, argv=argv)
        except Exception:
            self.cache.update_snippet(snippet.model_copy(update={"status": SnippetStatus.FAILED}))
            raise
        status = SnippetStatus.RAN if result.exit_code == 0 else SnippetStatus.FAILED
        self.cache.update_snippet(snippet.model_copy(update={"status": status}))
        return result

    def _run_mode(self, language: str | None) -> SnippetRunMode:
        normalized = (language or "").strip().lower()
        if normalized in {"bash", "sh", "shell", "zsh"}:
            return SnippetRunMode.SHELL_SCRIPT
        if normalized in {"python", "py"}:
            return SnippetRunMode.PYTHON_SCRIPT
        return SnippetRunMode.COPY_ONLY

    def _extension(self, snippet: Snippet) -> str:
        if snippet.run_mode is SnippetRunMode.SHELL_SCRIPT:
            return "sh"
        if snippet.run_mode is SnippetRunMode.PYTHON_SCRIPT:
            return "py"
        language = re.sub(r"[^a-z0-9_-]+", "", (snippet.language or "txt").lower())
        return language or "txt"

    def _stamp(self) -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S")
