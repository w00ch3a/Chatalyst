from __future__ import annotations

import asyncio
from dataclasses import dataclass

from loguru import logger
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header

from chatalyst.core.browser import BrowserController, BrowserUnavailableError
from chatalyst.core.cache import ChatCache
from chatalyst.core.chatgpt import ChatGPTService, SelectorResolutionError
from chatalyst.core.config import AppConfig
from chatalyst.core.export import ExportFormat, ExportService
from chatalyst.core.models import (
    BrowserSessionStatus,
    BrowserState,
    Conversation,
    LoginState,
    Message,
    MessageRole,
    Note,
    Snippet,
)
from chatalyst.core.obsidian import ObsidianDestinationRequired, ObsidianExportService
from chatalyst.core.plugins import PluginContext, PluginRegistry
from chatalyst.core.runtime import RuntimeLock
from chatalyst.core.search import SearchEngine
from chatalyst.core.snippets import SnippetService
from chatalyst.core.terminal import TerminalRunner, TerminalTimeoutError
from chatalyst.widgets.bookmark_panel import BookmarkPanel
from chatalyst.widgets.chat_list import ChatList
from chatalyst.widgets.command_palette import CommandPalette
from chatalyst.widgets.conversation_view import ConversationView
from chatalyst.widgets.message_input import MessageInput
from chatalyst.widgets.notes_panel import NotesPanel
from chatalyst.widgets.obsidian_dialog import ObsidianExportDialog
from chatalyst.widgets.search_dialog import SearchDialog
from chatalyst.widgets.snippet_panel import SnippetPanel
from chatalyst.widgets.status_bar import StatusBar


@dataclass(frozen=True)
class ObsidianCommand:
    selection: str = "conversation"
    destination: str | None = None
    body: str | None = None


def parse_obsidian_command(value: str) -> ObsidianCommand:
    raw = value.strip()
    for prefix in ("/obsidian", "/ov"):
        if raw == prefix:
            return ObsidianCommand()
        if raw.startswith(prefix + " "):
            raw = raw.removeprefix(prefix).strip()
            break
    else:
        return ObsidianCommand(destination=raw or None)
    if not raw:
        return ObsidianCommand()
    if raw.startswith("text "):
        return ObsidianCommand(selection="text", body=raw.removeprefix("text ").strip() or None)
    first, _, rest = raw.partition(" ")
    if first in {"last", "reply"}:
        return ObsidianCommand(selection="last", destination=rest.strip() or None)
    if first in {"visible", "view"}:
        return ObsidianCommand(selection="visible", destination=rest.strip() or None)
    return ObsidianCommand(destination=raw)


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
        ("o", "export_obsidian", "Obsidian"),
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
            self.obsidian_export = ObsidianExportService(config)
            self.terminal = TerminalRunner(
                cwd=config.workspace,
                timeout_seconds=config.terminal_timeout_seconds,
                output_limit=config.terminal_output_limit,
            )
            self.snippets = SnippetService(cache=self.cache, snippets_dir=config.snippets_dir)
            self.browser = BrowserController(config)
            self.chatgpt = ChatGPTService(config, self.browser, self.cache)
            self.plugins = PluginRegistry(audit_path=config.logs_dir / "plugin-audit.jsonl")
            self.plugins.load_from_directory(config.plugins_dir)
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
        elif command == "obsidian":
            self._export_current_to_obsidian()
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

    async def action_export_obsidian(self) -> None:
        self._export_current_to_obsidian()

    def _export_current_to_obsidian(
        self,
        command: ObsidianCommand | None = None,
    ) -> None:
        command = command or ObsidianCommand()
        if command.destination or self.obsidian_export.has_configured_vault():
            self._write_obsidian_export(command)
            return
        self.push_screen(
            ObsidianExportDialog(),
            lambda destination: self._handle_obsidian_destination(command, destination),
        )

    def _handle_obsidian_destination(
        self,
        command: ObsidianCommand,
        destination: str | None,
    ) -> None:
        if not destination:
            self.notify("Obsidian export cancelled.", severity="warning")
            return
        self._write_obsidian_export(
            ObsidianCommand(
                selection=command.selection,
                destination=destination,
                body=command.body,
            )
        )

    def _write_obsidian_export(self, command: ObsidianCommand) -> None:
        try:
            if command.selection == "conversation":
                if not self.current_conversation:
                    self.notify("Open a cached conversation first.", severity="warning")
                    return
                result = self.obsidian_export.export_conversation(
                    self.current_conversation,
                    self.current_messages,
                    destination=command.destination,
                )
            elif command.selection == "last":
                message = self._last_assistant_message()
                if not self.current_conversation or message is None:
                    self.notify("No cached ChatGPT reply is available.", severity="warning")
                    return
                result = self.obsidian_export.export_message(
                    self.current_conversation,
                    message,
                    destination=command.destination,
                )
            elif command.selection == "visible":
                body = self.query_one(ConversationView).last_markdown
                if not body.strip():
                    self.notify("Nothing visible to export.", severity="warning")
                    return
                title = (
                    self.current_conversation.title
                    if self.current_conversation
                    else "Chatalyst"
                )
                result = self.obsidian_export.export_markdown(
                    title=title,
                    body=body,
                    conversation_id=(
                        self.current_conversation.id if self.current_conversation else None
                    ),
                    destination=command.destination,
                    source="tui_visible",
                )
            elif command.selection == "text":
                if not command.body:
                    self.notify("No markdown text provided.", severity="warning")
                    return
                title = (
                    self.current_conversation.title
                    if self.current_conversation
                    else "Chatalyst"
                )
                result = self.obsidian_export.export_markdown(
                    title=title,
                    body=command.body,
                    conversation_id=(
                        self.current_conversation.id if self.current_conversation else None
                    ),
                    destination=command.destination,
                    source="tui_text",
                )
            else:
                self.notify(
                    f"Unknown Obsidian export target: {command.selection}",
                    severity="error",
                )
                return
        except ObsidianDestinationRequired:
            self._export_current_to_obsidian(command)
            return
        except Exception as exc:
            logger.exception("Obsidian export failed")
            self.notify(f"Obsidian export failed: {exc}", severity="error")
            return
        self.notify(f"Obsidian export wrote {result.path.name}")

    def _last_assistant_message(self) -> Message | None:
        for message in reversed(self.current_messages):
            if message.role is MessageRole.ASSISTANT:
                return message
        return self.current_messages[-1] if self.current_messages else None

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
        if value == "/ov" or value.startswith("/ov ") or value == "/obsidian" or value.startswith(
            "/obsidian "
        ):
            self._export_current_to_obsidian(parse_obsidian_command(value))
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
