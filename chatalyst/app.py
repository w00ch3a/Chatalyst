from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import termios
import tty
from pathlib import Path
from typing import TextIO

from loguru import logger
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header

from chatalyst.core.browser import BrowserController, BrowserUnavailableError
from chatalyst.core.cache import ChatCache
from chatalyst.core.chatgpt import ChatGPTService, SelectorResolutionError
from chatalyst.core.config import AppConfig
from chatalyst.core.export import ExportFormat, ExportService
from chatalyst.core.mcp_server import ChatalystMCPServer, run_stdio
from chatalyst.core.models import (
    BrowserSessionStatus,
    BrowserState,
    Conversation,
    LoginState,
    Note,
    Snippet,
)
from chatalyst.core.plugins import PluginContext, PluginRegistry
from chatalyst.core.runtime import RuntimeLock, RuntimeLockError
from chatalyst.core.search import SearchEngine
from chatalyst.core.snippets import SnippetService
from chatalyst.core.terminal import TerminalRunner, TerminalTimeoutError
from chatalyst.core.version import package_version
from chatalyst.widgets.bookmark_panel import BookmarkPanel
from chatalyst.widgets.chat_list import ChatList
from chatalyst.widgets.command_palette import CommandPalette
from chatalyst.widgets.conversation_view import ConversationView
from chatalyst.widgets.message_input import MessageInput
from chatalyst.widgets.notes_panel import NotesPanel
from chatalyst.widgets.search_dialog import SearchDialog
from chatalyst.widgets.snippet_panel import SnippetPanel
from chatalyst.widgets.status_bar import StatusBar


class ChatGPTTUI(App[None]):
    CSS = """
    Screen {
        layout: vertical;
    }
    #workspace {
        height: 1fr;
    }
    #conversation-pane {
        width: 1fr;
    }
    #compare-pane {
        width: 1fr;
        display: none;
    }
    """

    BINDINGS = [
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
        ("enter", "open_selected", "Open"),
        ("tab", "focus_next", "Next pane"),
        ("/", "search", "Search"),
        ("n", "new_chat", "New"),
        ("r", "refresh", "Refresh"),
        ("b", "bookmarks", "Bookmarks"),
        ("x", "stage_last_code", "Stage"),
        ("p", "palette", "Palette"),
        ("ctrl+p", "palette", "Palette"),
        ("ctrl+b", "safe_browser", "Browser"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config
        self.config.ensure_runtime_dirs()
        self.runtime_lock = RuntimeLock(config.runtime_lock_path)
        self.runtime_lock.acquire()
        try:
            logger.add(config.logs_dir / "chatgpt-tui.log", rotation="2 MB", retention=5)
            self.cache = ChatCache(config.database_path)
            self.cache.initialize()
            self.search_engine = SearchEngine(self.cache)
            self.export_service = ExportService(self.cache, config.exports_dir)
            self.terminal = TerminalRunner(
                cwd=config.workspace,
                timeout_seconds=config.terminal_timeout_seconds,
                output_limit=config.terminal_output_limit,
            )
            self.snippets = SnippetService(cache=self.cache, snippets_dir=config.snippets_dir)
            self.browser = BrowserController(config)
            self.chatgpt = ChatGPTService(config, self.browser, self.cache)
            self.plugins = PluginRegistry()
            self.plugin_context = PluginContext(config=config, cache=self.cache)
            self.status_model = BrowserSessionStatus(
                browser_state=BrowserState.STOPPED,
                login_state=LoginState.UNKNOWN,
                offline=config.offline,
            )
            self.current_conversation: Conversation | None = None
            self.current_messages = []
        except Exception:
            self.runtime_lock.release()
            raise

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="workspace"):
            yield ChatList(id="chat-list")
            with Vertical(id="conversation-pane"):
                yield ConversationView()
            with Vertical(id="compare-pane"):
                yield ConversationView(id="compare-view")
        yield MessageInput()
        yield StatusBar()
        yield Footer()

    async def on_mount(self) -> None:
        self.plugins.startup(self.plugin_context)
        await self._load_cached_chats()
        self.query_one(ChatList).focus()
        if self.config.offline:
            self._set_status(sync="offline")
            return
        asyncio.create_task(self._initial_online_sync())

    async def _initial_online_sync(self) -> None:
        self._set_status(sync="starting")
        try:
            self.status_model = await self.chatgpt.status()
            if self.status_model.login_state is LoginState.LOGGED_IN:
                await self.browser.restart_headless_after_login()
                await self.refresh_chats()
            else:
                self.query_one(ConversationView).show_diagnostic(
                    "Login Required",
                    "A Chromium window is open. Log in to ChatGPT there, "
                    "then press `r` to refresh.",
                )
        except Exception as exc:
            logger.exception("Initial sync failed")
            self.status_model = BrowserSessionStatus(
                browser_state=BrowserState.ERROR,
                login_state=LoginState.UNKNOWN,
                diagnostic=str(exc),
            )
            self.query_one(ConversationView).show_diagnostic(
                "Browser Diagnostic",
                f"Startup failed:\n\n```text\n{exc}\n```\n\n"
                "Press `Ctrl+B` to reveal/restart the browser.",
            )
        finally:
            if self.status_model.login_state is LoginState.LOGGED_IN:
                await self.browser.park_after_work()
            self._set_status()

    async def _load_cached_chats(self) -> None:
        conversations = self.cache.list_conversations()
        self.query_one(ChatList).load_conversations(conversations)
        if conversations and self.current_conversation is None:
            await self.open_conversation(conversations[0].id, browser_backed=False)

    async def refresh_chats(self) -> None:
        self._set_status(sync="syncing")
        try:
            if self.config.offline:
                await self._load_cached_chats()
                return
            await self.chatgpt.discover_chats()
            await self._load_cached_chats()
        except SelectorResolutionError as exc:
            self.query_one(ConversationView).show_diagnostic(
                "Selector Diagnostic",
                f"{exc.diagnostic.message}\n\nAttempted:\n\n"
                + "\n".join(f"- `{selector}`" for selector in exc.diagnostic.attempted),
            )
        except BrowserUnavailableError as exc:
            self.query_one(ConversationView).show_diagnostic("Browser Offline", str(exc))
        except Exception as exc:
            logger.exception("Refresh failed")
            self.query_one(ConversationView).show_diagnostic(
                "Refresh Failed", f"```text\n{exc}\n```"
            )
        finally:
            self._set_status()

    async def open_conversation(self, conversation_id: str, *, browser_backed: bool = True) -> None:
        self._set_status(sync="opening")
        try:
            if browser_backed and not self.config.offline:
                result = await self.chatgpt.open_conversation(conversation_id)
                conversation = result.conversation
                messages = result.messages
            else:
                conversation = self.cache.get_conversation(conversation_id)
                if conversation is None:
                    return
                self.cache.mark_opened(conversation_id)
                messages = self.cache.list_messages(conversation_id)
            self.current_conversation = conversation
            self.current_messages = messages
            self.plugins.conversation_opened(self.plugin_context, conversation_id)
            self.query_one(ConversationView).show_conversation(conversation, messages)
            self._set_status()
        except SelectorResolutionError as exc:
            self.query_one(ConversationView).show_diagnostic(
                "Selector Diagnostic", exc.diagnostic.message
            )
        except Exception as exc:
            logger.exception("Open conversation failed")
            self.query_one(ConversationView).show_diagnostic(
                "Open Failed", f"```text\n{exc}\n```"
            )
        finally:
            await self._load_cached_chats()
            await self.browser.park_after_work()
            self._set_status()

    async def action_cursor_down(self) -> None:
        self.query_one(ChatList).action_cursor_down()

    async def action_cursor_up(self) -> None:
        self.query_one(ChatList).action_cursor_up()

    async def action_open_selected(self) -> None:
        conversation = self.query_one(ChatList).selected_conversation
        if conversation:
            await self.open_conversation(conversation.id)

    async def action_refresh(self) -> None:
        await self.refresh_chats()

    async def action_new_chat(self) -> None:
        if self.config.offline:
            self.notify("Offline mode cannot create browser chats.", severity="warning")
            return
        conversation = await self.chatgpt.new_chat()
        self.current_conversation = conversation
        await self._load_cached_chats()
        self.query_one(MessageInput).focus()

    async def action_safe_browser(self) -> None:
        try:
            await self.browser.restart_visible()
            self.notify("Browser window revealed.")
        except Exception as exc:
            self.notify(f"Browser reveal failed: {exc}", severity="error")

    async def action_search(self) -> None:
        self.push_screen(SearchDialog(), self._handle_search_dialog)

    def _handle_search_dialog(self, value: str | None) -> None:
        if not value:
            return
        if self.cache.get_conversation(value):
            asyncio.create_task(
                self.open_conversation(value, browser_backed=not self.config.offline)
            )
            return
        results = self.plugins.search_results(
            self.plugin_context,
            value,
            self.search_engine.search(value),
        )
        self.push_screen(SearchDialog(results), self._handle_search_dialog)

    async def action_bookmarks(self) -> None:
        self.push_screen(BookmarkPanel(self.cache.list_bookmarks()), self._handle_bookmark_panel)

    def _handle_bookmark_panel(self, conversation_id: str | None) -> None:
        if conversation_id:
            asyncio.create_task(self.open_conversation(conversation_id, browser_backed=False))

    async def action_palette(self) -> None:
        self.push_screen(CommandPalette(), self._handle_palette)

    def _handle_palette(self, command: str | None) -> None:
        if not command:
            return
        if command == "search":
            asyncio.create_task(self.action_search())
        elif command == "refresh":
            asyncio.create_task(self.refresh_chats())
        elif command == "export":
            self._export_current()
        elif command == "bookmark":
            self._bookmark_current()
        elif command == "notes":
            self._open_notes()
        elif command == "recent":
            self.query_one(ChatList).load_conversations(self.cache.list_recent_conversations())
        elif command == "split":
            self._toggle_split()
        elif command == "open":
            asyncio.create_task(self.action_open_selected())
        elif command == "stage":
            asyncio.create_task(self.action_stage_last_code())

    def _open_notes(self) -> None:
        if not self.current_conversation:
            return
        notes = self.cache.list_notes(self.current_conversation.id)
        self.push_screen(NotesPanel(notes), self._handle_notes)

    def _handle_notes(self, body: str | None) -> None:
        if body is None or not self.current_conversation:
            return
        self.cache.upsert_note(Note(conversation_id=self.current_conversation.id, body=body))
        self.notify("Notes saved locally.")

    def _bookmark_current(self) -> None:
        if not self.current_conversation or not self.current_messages:
            return
        message = self.current_messages[-1]
        self.cache.bookmark_message(
            self.current_conversation.id,
            message.id,
            label=f"{self.current_conversation.title}: {message.role.value}",
        )
        self.notify("Bookmarked current message.")

    def _export_current(self) -> None:
        if not self.current_conversation:
            return
        path = self.export_service.export_conversation(
            self.current_conversation.id, ExportFormat.MARKDOWN
        )
        self.notify(f"Exported {path.name}")

    def _toggle_split(self) -> None:
        pane = self.query_one("#compare-pane")
        pane.styles.display = "block" if pane.styles.display == "none" else "none"
        if self.current_conversation:
            self.query_one("#compare-view", ConversationView).show_conversation(
                self.current_conversation,
                self.current_messages,
            )

    async def action_stage_last_code(self) -> None:
        if not self.current_conversation:
            self.notify("Open a cached conversation first.", severity="warning")
            return
        snippet = self.snippets.stage_last_code_block(self.current_conversation.id)
        if snippet is None:
            self.notify("No runnable code block found in this conversation.", severity="warning")
            return
        self.push_screen(
            SnippetPanel(snippet),
            lambda action: self._handle_snippet(snippet, action),
        )

    def _handle_snippet(self, snippet: Snippet, action: str | None) -> None:
        if action == "copy":
            self.copy_to_clipboard(snippet.body)
            self.notify("Snippet copied.")
        elif action == "save":
            self.notify(f"Snippet saved to {snippet.path}")
        elif action == "run":
            asyncio.create_task(self._run_snippet(snippet))

    async def _run_snippet(self, snippet: Snippet) -> None:
        self._set_status(sync="snippet")
        try:
            result = await self.snippets.run(snippet, self.terminal)
            self.query_one(ConversationView).show_diagnostic("Snippet Result", result.as_markdown())
        except Exception as exc:
            self.query_one(ConversationView).show_diagnostic(
                "Snippet Error",
                f"```text\n{exc}\n```",
            )
        finally:
            self._set_status()

    async def on_input_submitted(self, event: MessageInput.Submitted) -> None:
        value = event.value.strip()
        event.input.value = ""
        if value.startswith("/search "):
            results = self.search_engine.search(value.removeprefix("/search ").strip())
            self.push_screen(SearchDialog(results), self._handle_search_dialog)
            return
        if value.startswith("/tag ") and self.current_conversation:
            from chatalyst.core.models import Tag

            self.cache.apply_tag(
                self.current_conversation.id,
                Tag(name=value.removeprefix("/tag ")),
            )
            await self._load_cached_chats()
            return
        if value.startswith("/note ") and self.current_conversation:
            self.cache.upsert_note(
                Note(
                    conversation_id=self.current_conversation.id,
                    body=value.removeprefix("/note "),
                )
            )
            return
        if value.startswith("/terminal "):
            await self._run_terminal_command(value.removeprefix("/terminal ").strip())
            return
        if value == "/stage last":
            await self.action_stage_last_code()
            return
        if value.startswith("/stage "):
            self._stage_inline_text(value.removeprefix("/stage ").strip())
            return
        if not value:
            return
        if self.config.offline:
            self.notify(
                "Offline mode can browse, search, note, bookmark, and export only.",
                severity="warning",
            )
            return
        await self._send_prompt(value)

    async def _send_prompt(self, prompt: str) -> None:
        self._set_status(sync="streaming")
        try:
            async for message in self.chatgpt.send_message(prompt):
                conversation_id = message.conversation_id
                conversation = (
                    self.cache.get_conversation(conversation_id) or self.current_conversation
                )
                if conversation is None:
                    continue
                self.current_conversation = conversation
                self.current_messages = self.cache.list_messages(conversation.id)
                if message not in self.current_messages:
                    self.current_messages.append(message)
                self.query_one(ConversationView).show_streaming_message(
                    conversation,
                    self.current_messages,
                )
        except Exception as exc:
            logger.exception("Send failed")
            self.query_one(ConversationView).show_diagnostic("Send Failed", f"```text\n{exc}\n```")
        finally:
            await self._load_cached_chats()
            await self.browser.park_after_work()
            self._set_status()

    def _stage_inline_text(self, value: str) -> None:
        language = None
        body = value
        for prefix, detected in (
            ("bash ", "bash"),
            ("shell ", "bash"),
            ("sh ", "bash"),
            ("python ", "python"),
            ("py ", "python"),
            ("text ", None),
        ):
            if value.startswith(prefix):
                language = detected
                body = value.removeprefix(prefix)
                break
        if not body.strip():
            self.notify("No snippet text provided.", severity="warning")
            return
        snippet = self.snippets.stage_text(
            conversation_id=self.current_conversation.id if self.current_conversation else None,
            message_id=None,
            body=body,
            language=language,
        )
        self.push_screen(
            SnippetPanel(snippet),
            lambda action: self._handle_snippet(snippet, action),
        )

    async def _run_terminal_command(self, command: str) -> None:
        self._set_status(sync="terminal")
        try:
            result = await self.terminal.run(command)
            self.query_one(ConversationView).show_diagnostic("Terminal", result.as_markdown())
        except TerminalTimeoutError as exc:
            self.query_one(ConversationView).show_diagnostic(
                "Terminal Timeout",
                f"```text\n{exc}\n```",
            )
        except Exception as exc:
            self.query_one(ConversationView).show_diagnostic(
                "Terminal Error",
                f"```text\n{exc}\n```",
            )
        finally:
            self._set_status()

    def _set_status(self, *, sync: str = "idle") -> None:
        self.query_one(StatusBar).update_status(
            self.status_model,
            conversation=self.current_conversation,
            sync=sync,
        )

    async def on_unmount(self) -> None:
        try:
            await self.browser.stop()
        except Exception:
            logger.exception("Browser stop failed during shutdown")
        finally:
            try:
                self.cache.close()
            finally:
                self.runtime_lock.release()


async def run_interactive_login(config: AppConfig) -> int:
    """Open the persistent browser profile and wait for manual ChatGPT login."""

    config.ensure_runtime_dirs()
    runtime_lock = RuntimeLock(config.runtime_lock_path)
    runtime_lock.acquire()
    cache = ChatCache(config.database_path)
    browser: BrowserController | None = None
    try:
        logger.add(config.logs_dir / "chatgpt-tui.log", rotation="2 MB", retention=5)
        cache.initialize()
        browser = BrowserController(config)
        chatgpt = ChatGPTService(config, browser, cache)

        print("Opening ChatGPT in a persistent Chromium profile...")
        await browser.start(visible=True)
        await browser.open_chatgpt()
        print()
        print("Log in through the Chromium window.")
        print("When ChatGPT is fully open, return here and press Enter.")
        print("Leave this terminal waiting while you complete browser login, 2FA, passkeys, etc.")
        try:
            confirmed = await asyncio.to_thread(wait_for_terminal_return)
        except KeyboardInterrupt:
            print()
            print("Login wait cancelled; leaving the browser profile unchanged.")
            return 130
        if not confirmed:
            print("No terminal input was available; leaving the browser profile unchanged.")
            return 2

        status = await chatgpt.status()
        if status.login_state is LoginState.LOGGED_IN:
            storage_state = await browser.storage_state_path()
            print(f"Login detected and saved in {config.profile_dir}")
            print(f"Storage state snapshot written to {storage_state}")
            return 0

        print(f"Login was not detected yet: {status.login_state.value}")
        print("Run `chatalyst --login` again, or run `chatalyst --browser-mode visible`.")
        return 1
    finally:
        if browser is not None:
            await browser.stop()
        cache.close()
        runtime_lock.release()


def wait_for_terminal_return(stdin: TextIO | None = None) -> bool:
    """Wait for Return in terminals that send either LF or CR."""

    stream = stdin or sys.stdin
    if not stream.isatty():
        while True:
            char = stream.read(1)
            if char == "":
                return False
            if char in {"\n", "\r"}:
                return True

    fd = stream.fileno()
    old_attrs = termios.tcgetattr(fd)
    new_attrs = termios.tcgetattr(fd)
    new_attrs[3] &= ~(termios.ICANON | termios.ECHO)
    new_attrs[6][termios.VMIN] = 1
    new_attrs[6][termios.VTIME] = 0
    try:
        tty.setcbreak(fd)
        termios.tcsetattr(fd, termios.TCSADRAIN, new_attrs)
        while True:
            char = stream.read(1)
            if char in {"\n", "\r"}:
                print()
                return True
            if char == "\x03":
                raise KeyboardInterrupt
            if char == "\x04":
                print()
                return False
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)


def run_doctor(config: AppConfig, *, include_mcp: bool, max_text_chars: int) -> int:
    """Run local configuration checks without opening ChatGPT."""

    config.ensure_runtime_dirs()
    server = ChatalystMCPServer(config, read_only=config.offline, max_text_chars=max_text_chars)
    try:
        counts = server._cache_counts()
        scope = server._tool_get_scope({})
        tools = server._tools() if include_mcp else []
    finally:
        server.cache.close()

    paths = {
        "workspace": config.workspace,
        "database": config.database_path,
        "profile": config.profile_dir,
        "logs": config.logs_dir,
        "exports": config.exports_dir,
        "snippets": config.snippets_dir,
    }
    path_status = {
        name: {
            "path": str(path),
            "exists": path.exists(),
            "private_mode": _private_mode(path),
        }
        for name, path in paths.items()
    }
    payload = {
        "ok": True,
        "workspace": str(config.workspace),
        "offline": config.offline,
        "browser_mode": config.browser_mode.value,
        "browser_profile": config.browser_profile.value,
        "commands": {
            "chatalyst": shutil.which("chatalyst"),
            "chatalyst-mcp": shutil.which("chatalyst-mcp"),
            "chatgpt-tui": shutil.which("chatgpt-tui"),
        },
        "paths": path_status,
        "cache_counts": counts,
        "scope": scope,
        "mcp": {
            "checked": include_mcp,
            "tool_count": len(tools),
            "tools": [tool["name"] for tool in tools],
        },
    }
    print(json.dumps(payload, indent=2))
    return 0


def _private_mode(path: Path) -> str | None:
    if not path.exists():
        return None
    return oct(path.stat().st_mode & 0o777)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chatalyst")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {package_version()}",
        help="Show the installed Chatalyst version and exit.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="Chatalyst workspace containing profile/, storage/, exports/, and work/.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Browse cached chats without browser access.",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Open the persistent Chromium profile for manual ChatGPT login, then wait.",
    )
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Run/check the local-vault MCP server instead of the TUI.",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Check local Chatalyst workspace, install, vault, and optional MCP schema.",
    )
    parser.add_argument(
        "--mcp-read-only",
        action="store_true",
        help="When used with --mcp, expose only read-only vault tools.",
    )
    parser.add_argument(
        "--mcp-max-request-bytes",
        type=int,
        default=1_000_000,
        help="When used with --mcp, cap each JSON-RPC request read from stdin.",
    )
    parser.add_argument(
        "--mcp-max-text-chars",
        type=int,
        default=100_000,
        help="When used with --mcp, cap text bodies accepted by write-capable tools.",
    )
    parser.add_argument(
        "--assistant-response-timeout-seconds",
        type=float,
        default=300.0,
        help="Maximum wait for ChatGPT to start/finish a live assistant response.",
    )
    parser.add_argument(
        "--mcp-live-response-timeout-seconds",
        type=float,
        default=75.0,
        help="Default MCP live send/reply wait before returning submitted_no_response.",
    )
    parser.add_argument(
        "--mcp-live-result-message-limit",
        type=int,
        default=20,
        help="Recent messages returned by live MCP send/reply tools; full history remains cached.",
    )
    parser.add_argument(
        "--mcp-default-conversation",
        help=(
            "Optional MCP default conversation id, URL, or title used when reply tools "
            "omit conversation_id."
        ),
    )
    parser.add_argument(
        "--mcp-default-project",
        help=(
            "Optional MCP default project name; reply tools use the most recent cached "
            "conversation in that project when conversation_id is omitted."
        ),
    )
    parser.add_argument("--debug", action="store_true", help="Enable verbose diagnostics.")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Shortcut for --browser-mode headless. Best for SSH after login is saved.",
    )
    parser.add_argument(
        "--browser-mode",
        choices=("auto", "provider", "background", "visible", "headless", "sleep"),
        default="auto",
        help=(
            "Browser lifecycle: auto shows login then goes headless; provider uses a "
            "disposable hidden headed Chromium provider for live work; visible always "
            "shows; headless always hides; sleep closes Chromium between live operations."
        ),
    )
    parser.add_argument(
        "--browser-profile",
        choices=("standard", "ultralight"),
        default="standard",
        help="Browser resource policy. Ultralight blocks more assets and keeps less DOM visible.",
    )
    parser.add_argument(
        "--host-mode",
        choices=("single",),
        default="single",
        help="Network host posture. Currently single-user SSH hosting with a runtime lock.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.login and args.mcp:
        parser.error("--login cannot be combined with --mcp")
    if args.login and args.doctor:
        parser.error("--login cannot be combined with --doctor")
    browser_mode = "headless" if args.headless else args.browser_mode
    workspace = args.workspace.expanduser().resolve()
    if args.doctor:
        mcp_browser_mode = browser_mode
        if (
            args.mcp
            and not args.mcp_read_only
            and not args.headless
            and args.browser_mode == "auto"
        ):
            mcp_browser_mode = "provider"
        config = AppConfig.from_workspace(
            workspace,
            offline=args.offline or args.mcp_read_only,
            debug=args.debug,
            headless=args.headless,
            browser_mode=mcp_browser_mode,
            browser_profile=args.browser_profile,
        ).model_copy(
            update={
                "assistant_response_timeout_seconds": args.assistant_response_timeout_seconds,
                "mcp_live_response_timeout_seconds": args.mcp_live_response_timeout_seconds,
                "mcp_live_result_message_limit": args.mcp_live_result_message_limit,
                "mcp_default_conversation": args.mcp_default_conversation,
                "mcp_default_project": args.mcp_default_project,
            }
        )
        raise SystemExit(
            run_doctor(config, include_mcp=args.mcp, max_text_chars=args.mcp_max_text_chars)
        )
    if args.login:
        config = AppConfig.from_workspace(
            workspace,
            offline=False,
            debug=args.debug,
            headless=False,
            browser_mode="visible",
            browser_profile=args.browser_profile,
        ).model_copy(
            update={
                "assistant_response_timeout_seconds": args.assistant_response_timeout_seconds,
                "mcp_live_response_timeout_seconds": args.mcp_live_response_timeout_seconds,
                "mcp_live_result_message_limit": args.mcp_live_result_message_limit,
            }
        )
        try:
            raise SystemExit(asyncio.run(run_interactive_login(config)))
        except RuntimeLockError as exc:
            print(exc)
            raise SystemExit(2) from exc
    if args.mcp:
        mcp_browser_mode = browser_mode
        if not args.mcp_read_only and not args.headless and args.browser_mode == "auto":
            mcp_browser_mode = "provider"
        config = AppConfig.from_workspace(
            workspace,
            offline=args.offline or args.mcp_read_only,
            debug=args.debug,
            headless=args.headless,
            browser_mode=mcp_browser_mode,
            browser_profile=args.browser_profile,
        ).model_copy(
            update={
                "assistant_response_timeout_seconds": args.assistant_response_timeout_seconds,
                "mcp_live_response_timeout_seconds": args.mcp_live_response_timeout_seconds,
                "mcp_live_result_message_limit": args.mcp_live_result_message_limit,
                "mcp_default_conversation": args.mcp_default_conversation,
                "mcp_default_project": args.mcp_default_project,
            }
        )
        server = ChatalystMCPServer(
            config,
            read_only=args.offline or args.mcp_read_only,
            max_text_chars=args.mcp_max_text_chars,
        )
        raise SystemExit(run_stdio(server, max_request_bytes=args.mcp_max_request_bytes))
    config = AppConfig.from_workspace(
        workspace,
        offline=args.offline,
        debug=args.debug,
        headless=args.headless,
        browser_mode=browser_mode,
        browser_profile=args.browser_profile,
    ).model_copy(
        update={
            "assistant_response_timeout_seconds": args.assistant_response_timeout_seconds,
            "mcp_live_response_timeout_seconds": args.mcp_live_response_timeout_seconds,
            "mcp_live_result_message_limit": args.mcp_live_result_message_limit,
        }
    )
    try:
        ChatGPTTUI(config).run()
    except RuntimeLockError as exc:
        print(exc)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
