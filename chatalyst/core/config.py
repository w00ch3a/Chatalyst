from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

_ACCOUNT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def validate_account_name(account: str | None) -> str | None:
    """Return a safe account slug or reject path-like/private names."""

    if account is None:
        return None
    stripped = account.strip()
    if not stripped:
        raise ValueError("account must not be blank")
    if not _ACCOUNT_NAME_RE.fullmatch(stripped):
        raise ValueError(
            "account must start with a letter or number and contain only "
            "letters, numbers, dots, underscores, or hyphens"
        )
    if stripped in {".", ".."} or stripped.startswith("."):
        raise ValueError("account must not be hidden or path-like")
    return stripped


class BrowserMode(StrEnum):
    AUTO = "auto"
    BACKGROUND = "background"
    PROVIDER = "provider"
    VISIBLE = "visible"
    HEADLESS = "headless"
    SLEEP = "sleep"


class BrowserProfile(StrEnum):
    STANDARD = "standard"
    ULTRALIGHT = "ultralight"


class AppConfig(BaseModel):
    app_name: str = "chatgpt-tui"
    workspace: Path
    account: str | None = None
    account_dir: Path | None = None
    database_path: Path
    profile_dir: Path
    logs_dir: Path
    exports_dir: Path
    config_dir: Path
    plugins_dir: Path
    runtime_dir: Path
    snippets_dir: Path
    offline: bool = False
    debug: bool = False
    headless: bool = False
    browser_mode: BrowserMode = BrowserMode.AUTO
    browser_profile: BrowserProfile = BrowserProfile.STANDARD
    chatgpt_url: str = "https://chatgpt.com/"
    launch_timeout_ms: int = 60_000
    terminal_timeout_seconds: float = 30.0
    terminal_output_limit: int = 24_000
    response_poll_interval_seconds: float = 0.35
    selector_timeout_ms: int = 10_000
    max_stream_idle_seconds: float = 2.0
    live_tool_lock_timeout_seconds: float = 180.0
    assistant_response_timeout_seconds: float = 300.0
    mcp_live_response_timeout_seconds: float = 180.0
    mcp_live_result_message_limit: int = 20
    mcp_token_frugal: bool = False
    mcp_prompt_warning_tokens: int = 4_000
    mcp_default_conversation: str | None = None
    mcp_default_project: str | None = None
    browser_viewport_width: int = 1440
    browser_viewport_height: int = 1000
    browser_retain_recent_turns: int = 12
    browser_retain_sidebar_items: int = 80
    supported_export_formats: tuple[str, ...] = ("markdown", "html", "json", "txt")
    supported_code_languages: tuple[str, ...] = Field(
        default=(
            "python",
            "bash",
            "shell",
            "yaml",
            "json",
            "dockerfile",
            "javascript",
            "typescript",
            "go",
            "rust",
            "terraform",
            "sql",
        )
    )

    @classmethod
    def from_workspace(
        cls,
        workspace: Path | str,
        *,
        offline: bool = False,
        debug: bool = False,
        headless: bool = False,
        browser_mode: BrowserMode | str = BrowserMode.AUTO,
        browser_profile: BrowserProfile | str = BrowserProfile.STANDARD,
        account: str | None = None,
    ) -> AppConfig:
        root = Path(workspace).expanduser().resolve()
        account_name = validate_account_name(account)
        base = (root / "accounts" / account_name).resolve() if account_name else root
        resolved_mode = BrowserMode(browser_mode)
        resolved_profile = BrowserProfile(browser_profile)
        resolved_headless = headless or resolved_mode in {BrowserMode.HEADLESS, BrowserMode.SLEEP}
        viewport_width = 1000 if resolved_profile is BrowserProfile.ULTRALIGHT else 1440
        viewport_height = 720 if resolved_profile is BrowserProfile.ULTRALIGHT else 1000
        retain_recent_turns = 4 if resolved_profile is BrowserProfile.ULTRALIGHT else 12
        retain_sidebar_items = 20 if resolved_profile is BrowserProfile.ULTRALIGHT else 80
        return cls(
            workspace=root,
            account=account_name,
            account_dir=base if account_name else None,
            database_path=base / "storage" / "chat_cache.db",
            profile_dir=base / "profile" / "chromium",
            logs_dir=base / "logs",
            exports_dir=base / "exports",
            config_dir=base / "config",
            plugins_dir=base / "plugins",
            runtime_dir=base / "runtime",
            snippets_dir=base / "work" / "snippets",
            offline=offline,
            debug=debug,
            headless=resolved_headless,
            browser_mode=resolved_mode,
            browser_profile=resolved_profile,
            browser_viewport_width=viewport_width,
            browser_viewport_height=viewport_height,
            browser_retain_recent_turns=retain_recent_turns,
            browser_retain_sidebar_items=retain_sidebar_items,
        )

    def ensure_runtime_dirs(self) -> None:
        paths = [
            self.database_path.parent,
            self.profile_dir,
            self.logs_dir,
            self.exports_dir,
            self.config_dir,
            self.plugins_dir,
            self.runtime_dir,
            self.snippets_dir,
        ]
        if self.account_dir is not None:
            paths.insert(0, self.account_dir)
        for path in paths:
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(0o700)

    @property
    def runtime_lock_path(self) -> Path:
        return self.runtime_dir / "default.lock"

    @property
    def project_aliases_path(self) -> Path:
        return self.config_dir / "project_aliases.json"
