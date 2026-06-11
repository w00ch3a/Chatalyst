from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from loguru import logger

from chatalyst.core.browser import BrowserController
from chatalyst.core.cache import ChatCache
from chatalyst.core.chatgpt import (
    ChatGPTService,
    ProjectSelectionError,
    PromptSubmittedNoAssistantResponseError,
)
from chatalyst.core.config import AppConfig
from chatalyst.core.export import ExportFormat, ExportService
from chatalyst.core.models import Conversation, LoginState, Message
from chatalyst.core.runtime import RuntimeLock, RuntimeLockError
from chatalyst.core.search import SearchEngine
from chatalyst.core.snippets import SnippetService
from chatalyst.core.version import package_version

JsonObject = dict[str, Any]
ToolHandler = Callable[[JsonObject], JsonObject | Awaitable[JsonObject]]


class MCPError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ChatalystMCPServer:
    """Minimal stdio MCP server for the local Chatalyst vault.

    The server deliberately exposes local knowledge-workspace operations only.
    Browser automation and terminal execution stay inside the interactive app.
    """

    def __init__(
        self,
        config: AppConfig,
        *,
        read_only: bool = False,
        max_text_chars: int = 100_000,
    ) -> None:
        self.config = config
        self.read_only = read_only
        self.max_text_chars = max_text_chars
        self.cache = ChatCache(self.config.database_path)
        self.cache.initialize()
        self.search = SearchEngine(self.cache)
        self.export = ExportService(self.cache, self.config.exports_dir)
        self.snippets = SnippetService(cache=self.cache, snippets_dir=self.config.snippets_dir)
        self.browser: BrowserController | None = None
        self.chatgpt: ChatGPTService | None = None
        self._tool_handlers: dict[str, ToolHandler] = {
            "chatalyst_health": self._tool_health,
            "chatalyst_get_scope": self._tool_get_scope,
            "chatalyst_search": self._tool_search,
            "chatalyst_list_conversations": self._tool_list_conversations,
            "chatalyst_list_projects": self._tool_list_projects,
            "chatalyst_get_conversation": self._tool_get_conversation,
            "chatalyst_list_bookmarks": self._tool_list_bookmarks,
        }
        if not self.read_only:
            self._tool_handlers.update(
                {
                    "chatalyst_export_conversation": self._tool_export_conversation,
                    "chatalyst_stage_snippet": self._tool_stage_snippet,
                    "chatalyst_send_new_message": self._tool_send_new_message,
                    "chatalyst_reply_to_conversation": self._tool_reply_to_conversation,
                }
            )

    async def close(self) -> None:
        try:
            if self.browser is not None:
                await self.browser.stop()
        finally:
            self.cache.close()

    async def handle(self, request: JsonObject) -> JsonObject | None:
        request_id = request.get("id")
        if request_id is None:
            return None
        try:
            method = self._require_str(request, "method")
            params = request.get("params") or {}
            if not isinstance(params, dict):
                raise MCPError(-32602, "params must be an object.")
            result = await self._dispatch(method, params)
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except MCPError as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": exc.code, "message": exc.message},
            }
        except Exception:  # pragma: no cover - defensive MCP boundary
            logger.exception("Unhandled MCP request failure")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": "Internal error."},
            }

    async def _dispatch(self, method: str, params: JsonObject) -> JsonObject:
        if method == "initialize":
            protocol_version = params.get("protocolVersion") or "2024-11-05"
            return {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "chatalyst", "version": self._package_version()},
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": self._tools()}
        if method == "tools/call":
            return await self._call_tool(params)
        raise MCPError(-32601, f"Unsupported MCP method: {method}")

    async def _call_tool(self, params: JsonObject) -> JsonObject:
        name = self._require_str(params, "name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise MCPError(-32602, "Tool arguments must be an object.")
        handler = self._tool_handlers.get(name)
        if handler is None:
            raise MCPError(-32601, f"Unknown Chatalyst tool: {name}")
        result = handler(arguments)
        if inspect.isawaitable(result):
            result = await result
        return self._text_result(result)

    def _tool_search(self, arguments: JsonObject) -> JsonObject:
        query = self._require_bounded_str(arguments, "query", maximum=2_000).strip()
        limit = self._limit(arguments.get("limit"), default=20, maximum=100)
        if not query:
            raise MCPError(-32602, "query must not be empty.")
        results = self.search.search(query, limit=limit)
        return {
            "query": query,
            "results": [result.model_dump(mode="json") for result in results],
        }

    async def _tool_health(self, arguments: JsonObject) -> JsonObject:
        check_browser = self._optional_bool(arguments, "check_browser", default=False)
        resolved_conversation = self._resolve_scoped_conversation(required=False)
        resolved_project = (
            self._find_recent_project_conversation(self.config.mcp_default_project)
            if self.config.mcp_default_project
            else None
        )
        payload: JsonObject = {
            "version": self._package_version(),
            "workspace": str(self.config.workspace),
            "database_path": str(self.config.database_path),
            "read_only": self.read_only,
            "offline": self.config.offline,
            "browser_mode": self.config.browser_mode.value,
            "browser_profile": self.config.browser_profile.value,
            "default_conversation": self.config.mcp_default_conversation,
            "default_project": self.config.mcp_default_project,
            "resolved_conversation": (
                resolved_conversation.model_dump(mode="json")
                if resolved_conversation is not None
                else None
            ),
            "default_project_has_cached_conversation": resolved_project is not None,
            "projects": [project.model_dump(mode="json") for project in self.cache.list_projects()],
            "cache_counts": self._cache_counts(),
            "runtime_lock": self._runtime_lock_status(),
            "browser": {
                "checked": False,
                "state": self.browser.status.browser_state.value if self.browser else "not_started",
                "login_state": self.browser.status.login_state.value if self.browser else "unknown",
            },
        }
        if check_browser:
            chatgpt = await self._live_chatgpt()
            status = await chatgpt.status()
            payload["browser"] = {
                "checked": True,
                "state": status.browser_state.value,
                "login_state": status.login_state.value,
                "diagnostic": status.diagnostic,
            }
            await self._park_browser()
        return payload

    def _tool_get_scope(self, arguments: JsonObject) -> JsonObject:
        del arguments
        conversation = self._resolve_scoped_conversation(required=False)
        return {
            "default_conversation": self.config.mcp_default_conversation,
            "default_project": self.config.mcp_default_project,
            "resolved_conversation": (
                conversation.model_dump(mode="json") if conversation is not None else None
            ),
        }

    def _tool_list_conversations(self, arguments: JsonObject) -> JsonObject:
        limit = self._limit(arguments.get("limit"), default=50, maximum=250)
        conversations = self.cache.list_conversations()[:limit]
        return {
            "conversations": [
                conversation.model_dump(mode="json") for conversation in conversations
            ],
        }

    def _tool_list_projects(self, arguments: JsonObject) -> JsonObject:
        limit = self._limit(arguments.get("limit"), default=100, maximum=500)
        projects = self.cache.list_projects()[:limit]
        return {
            "projects": [project.model_dump(mode="json") for project in projects],
            "count": len(projects),
        }

    def _tool_get_conversation(self, arguments: JsonObject) -> JsonObject:
        conversation_id = self._require_bounded_str(arguments, "conversation_id", maximum=500)
        conversation = self.cache.get_conversation(conversation_id)
        if conversation is None:
            raise MCPError(-32602, f"Conversation not found: {conversation_id}")
        include_messages = self._optional_bool(arguments, "include_messages", default=True)
        all_messages = self.cache.list_messages(conversation_id)
        message_count = len(all_messages)
        messages: list[Message] = []
        if include_messages:
            messages = self._bounded_messages(all_messages, arguments)
        notes = self.cache.list_notes(conversation_id)
        tags = self.cache.list_tags(conversation_id)
        stats = self.cache.conversation_stats(conversation_id)
        return {
            "conversation": conversation.model_dump(mode="json"),
            "messages": [message.model_dump(mode="json") for message in messages],
            "notes": [note.model_dump(mode="json") for note in notes],
            "tags": [tag.model_dump(mode="json") for tag in tags],
            "stats": stats.model_dump(mode="json") if stats else None,
            "message_count": message_count,
            "messages_returned": len(messages),
            "messages_truncated": include_messages and len(messages) < message_count,
        }

    def _tool_list_bookmarks(self, arguments: JsonObject) -> JsonObject:
        conversation_id = arguments.get("conversation_id")
        if conversation_id is not None and not isinstance(conversation_id, str):
            raise MCPError(-32602, "conversation_id must be a string when provided.")
        bookmarks = self.cache.list_bookmarks(conversation_id)
        return {
            "bookmarks": [bookmark.model_dump(mode="json") for bookmark in bookmarks],
        }

    def _tool_export_conversation(self, arguments: JsonObject) -> JsonObject:
        self._require_write_enabled()
        conversation_id = self._require_bounded_str(arguments, "conversation_id", maximum=500)
        format_value = self._require_bounded_str(arguments, "format", maximum=20)
        try:
            export_format = ExportFormat(format_value)
        except ValueError as exc:
            formats = ", ".join(item.value for item in ExportFormat)
            raise MCPError(-32602, f"format must be one of: {formats}") from exc
        selected_message_ids = arguments.get("selected_message_ids")
        if selected_message_ids is not None:
            if not isinstance(selected_message_ids, list) or not all(
                isinstance(item, str) for item in selected_message_ids
            ):
                raise MCPError(-32602, "selected_message_ids must be a list of strings.")
        path = self.export.export_conversation(
            conversation_id,
            export_format,
            selected_message_ids=selected_message_ids,
        )
        return {"path": str(path), "format": export_format.value}

    def _tool_stage_snippet(self, arguments: JsonObject) -> JsonObject:
        self._require_write_enabled()
        body = self._require_bounded_str(arguments, "body", maximum=self.max_text_chars)
        if not body.strip():
            raise MCPError(-32602, "body must not be empty.")
        conversation_id = self._optional_str(arguments, "conversation_id")
        message_id = self._optional_str(arguments, "message_id")
        language = self._optional_str(arguments, "language")
        snippet = self.snippets.stage_text(
            conversation_id=conversation_id,
            message_id=message_id,
            body=body,
            language=language,
        )
        return {"snippet": snippet.model_dump(mode="json")}

    async def _tool_send_new_message(self, arguments: JsonObject) -> JsonObject:
        self._require_write_enabled()
        prompt = self._require_bounded_str(arguments, "prompt", maximum=self.max_text_chars)
        if not prompt.strip():
            raise MCPError(-32602, "prompt must not be empty.")
        wait_seconds = self._optional_wait_seconds(arguments)
        project_name = self._optional_project_name(arguments)
        try:
            async with RuntimeLock(
                self.config.runtime_lock_path,
                timeout_seconds=self.config.live_tool_lock_timeout_seconds,
            ):
                chatgpt = await self._live_chatgpt()
                try:
                    await chatgpt.new_chat(project_name=project_name)
                    result = await self._send_prompt_and_payload(
                        prompt,
                        wait_seconds=wait_seconds,
                        project_name=project_name,
                    )
                    if project_name is not None:
                        scope = await chatgpt.verify_project_scope(project_name)
                        result["scope"] = {
                            "requested_project": scope.requested_project,
                            "verified": scope.verified,
                            "reason": scope.reason,
                            "url": scope.url,
                        }
                        if not scope.verified and result.get("status") is None:
                            result["status"] = "scope_uncertain"
                    return result
                finally:
                    await self._park_browser()
        except RuntimeLockError as exc:
            raise MCPError(-32000, str(exc)) from exc
        except ProjectSelectionError as exc:
            raise MCPError(-32000, str(exc)) from exc

    async def _tool_reply_to_conversation(self, arguments: JsonObject) -> JsonObject:
        self._require_write_enabled()
        prompt = self._require_bounded_str(arguments, "prompt", maximum=self.max_text_chars)
        if not prompt.strip():
            raise MCPError(-32602, "prompt must not be empty.")
        wait_seconds = self._optional_wait_seconds(arguments)
        try:
            async with RuntimeLock(
                self.config.runtime_lock_path,
                timeout_seconds=self.config.live_tool_lock_timeout_seconds,
            ):
                conversation = self._resolve_scoped_conversation(
                    arguments.get("conversation_id"),
                    required=True,
                )
                if conversation is None:
                    raise MCPError(-32602, "conversation_id could not be resolved.")
                chatgpt = await self._live_chatgpt()
                try:
                    opened = await chatgpt.open_conversation(conversation.id)
                    result = await self._send_prompt_and_payload(
                        prompt,
                        wait_seconds=wait_seconds,
                    )
                    if result.get("conversation") is None:
                        result["conversation"] = opened.conversation.model_dump(mode="json")
                    return result
                finally:
                    await self._park_browser()
        except RuntimeLockError as exc:
            raise MCPError(-32000, str(exc)) from exc

    def _tools(self) -> list[JsonObject]:
        tools = [
            {
                "name": "chatalyst_health",
                "description": (
                    "Return Chatalyst MCP health, local vault counts, configured scope, "
                    "browser mode/profile, and optional live browser login status."
                ),
                "annotations": {"readOnlyHint": True},
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "check_browser": {
                            "type": "boolean",
                            "description": "When true, briefly starts the browser provider.",
                        }
                    },
                },
            },
            {
                "name": "chatalyst_get_scope",
                "description": (
                    "Show Chatalyst's configured default conversation/project scope. "
                    "Use this before live reply/new-chat calls when scope matters."
                ),
                "annotations": {"readOnlyHint": True},
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "chatalyst_search",
                "description": (
                    "Search only the local Chatalyst cache: cached ChatGPT "
                    "conversation titles/messages, notes, tags, and bookmarks. "
                    "For fresh research, use a live ChatGPT send/reply tool instead."
                ),
                "annotations": {"readOnlyHint": True},
                "inputSchema": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                },
            },
            {
                "name": "chatalyst_list_conversations",
                "description": (
                    "List locally cached Chatalyst conversations, pinned and recent "
                    "first. Use this to find conversation_id values for get/reply."
                ),
                "annotations": {"readOnlyHint": True},
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 250},
                    },
                },
            },
            {
                "name": "chatalyst_list_projects",
                "description": (
                    "List locally cached ChatGPT projects discovered from the visible "
                    "ChatGPT sidebar/project UI."
                ),
                "annotations": {"readOnlyHint": True},
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                    },
                },
            },
            {
                "name": "chatalyst_get_conversation",
                "description": (
                    "Read one locally cached Chatalyst conversation with messages, "
                    "notes, tags, and stats. Does not refresh ChatGPT or browse the web."
                ),
                "annotations": {"readOnlyHint": True},
                "inputSchema": {
                    "type": "object",
                    "required": ["conversation_id"],
                    "properties": {
                        "conversation_id": {"type": "string"},
                        "include_messages": {"type": "boolean"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                        "offset": {"type": "integer", "minimum": 0},
                        "before_ordinal": {"type": "integer", "minimum": 0},
                    },
                },
            },
            {
                "name": "chatalyst_list_bookmarks",
                "description": (
                    "List locally cached Chatalyst bookmarks, optionally scoped to "
                    "one conversation."
                ),
                "annotations": {"readOnlyHint": True},
                "inputSchema": {
                    "type": "object",
                    "properties": {"conversation_id": {"type": "string"}},
                },
            },
        ]
        if self.read_only:
            return tools
        tools.extend(
            [
                {
                    "name": "chatalyst_export_conversation",
                    "description": (
                        "Write a local export file for one cached Chatalyst "
                        "conversation in Markdown, HTML, JSON, or TXT."
                    ),
                    "annotations": {"readOnlyHint": False, "destructiveHint": False},
                    "inputSchema": {
                        "type": "object",
                        "required": ["conversation_id", "format"],
                        "properties": {
                            "conversation_id": {"type": "string"},
                            "format": {
                                "type": "string",
                                "enum": ["markdown", "html", "json", "txt"],
                            },
                            "selected_message_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                },
                {
                    "name": "chatalyst_stage_snippet",
                    "description": (
                        "Stage text or code into Chatalyst's local snippet workspace "
                        "for later user review. Does not execute terminal commands."
                    ),
                    "annotations": {"readOnlyHint": False, "destructiveHint": False},
                    "inputSchema": {
                        "type": "object",
                        "required": ["body"],
                        "properties": {
                            "body": {"type": "string"},
                            "language": {"type": "string"},
                            "conversation_id": {"type": "string"},
                            "message_id": {"type": "string"},
                        },
                    },
                },
                {
                    "name": "chatalyst_send_new_message",
                    "description": (
                        "Create a new ChatGPT conversation through Chatalyst's "
                        "authenticated browser provider, then send this prompt. "
                        "Use this for fresh ChatGPT work, including research tasks, "
                        "when a new conversation is appropriate. Uses the configured "
                        "default project when one is set, unless project_name is "
                        "provided."
                    ),
                    "annotations": {"readOnlyHint": False, "destructiveHint": False},
                    "inputSchema": {
                        "type": "object",
                        "required": ["prompt"],
                        "properties": {
                            "prompt": {"type": "string"},
                            "project_name": {"type": "string"},
                            "wait_for_response_seconds": {
                                "type": "number",
                                "minimum": 5,
                                "maximum": 900,
                            },
                        },
                    },
                },
                {
                    "name": "chatalyst_reply_to_conversation",
                    "description": (
                        "Reply in an existing ChatGPT conversation through "
                        "Chatalyst's authenticated browser provider. Use for "
                        "conversation handoff, coordination with ChatGPT, or "
                        "continuing research/work in a scoped existing thread. "
                        "conversation_id is optional when launched with a default "
                        "conversation or project scope."
                    ),
                    "annotations": {"readOnlyHint": False, "destructiveHint": False},
                    "inputSchema": {
                        "type": "object",
                        "required": ["prompt"],
                        "properties": {
                            "conversation_id": {"type": "string"},
                            "prompt": {"type": "string"},
                            "wait_for_response_seconds": {
                                "type": "number",
                                "minimum": 5,
                                "maximum": 900,
                            },
                        },
                    },
                },
            ]
        )
        return tools

    def _text_result(self, payload: JsonObject) -> JsonObject:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(payload, indent=2, ensure_ascii=False),
                }
            ]
        }

    def _require_str(self, data: JsonObject, key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str):
            raise MCPError(-32602, f"{key} must be a string.")
        return value

    def _require_bounded_str(self, data: JsonObject, key: str, *, maximum: int) -> str:
        value = self._require_str(data, key)
        if len(value) > maximum:
            raise MCPError(-32602, f"{key} exceeds {maximum} characters.")
        return value

    def _optional_str(self, data: JsonObject, key: str) -> str | None:
        value = data.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise MCPError(-32602, f"{key} must be a string when provided.")
        return value

    def _limit(self, value: object, *, default: int, maximum: int) -> int:
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise MCPError(-32602, "limit must be a positive integer.")
        return min(value, maximum)

    def _optional_bool(self, data: JsonObject, key: str, *, default: bool) -> bool:
        value = data.get(key)
        if value is None:
            return default
        if not isinstance(value, bool):
            raise MCPError(-32602, f"{key} must be a boolean.")
        return value

    def _optional_nonnegative_int(self, data: JsonObject, key: str) -> int | None:
        value = data.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise MCPError(-32602, f"{key} must be a non-negative integer.")
        return value

    def _optional_wait_seconds(self, arguments: JsonObject) -> float:
        value = arguments.get("wait_for_response_seconds")
        if value is None:
            return self.config.mcp_live_response_timeout_seconds
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise MCPError(-32602, "wait_for_response_seconds must be a number.")
        if value < 5 or value > 900:
            raise MCPError(-32602, "wait_for_response_seconds must be between 5 and 900.")
        return float(value)

    def _optional_project_name(self, arguments: JsonObject) -> str | None:
        value = self._optional_str(arguments, "project_name")
        if value is None:
            value = self.config.mcp_default_project
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise MCPError(-32602, "project_name must not be blank.")
        if len(stripped) > 200:
            raise MCPError(-32602, "project_name exceeds 200 characters.")
        return stripped

    def _resolve_scoped_conversation(
        self,
        requested: object | None = None,
        *,
        required: bool,
    ) -> Conversation | None:
        if requested is not None:
            if not isinstance(requested, str) or not requested.strip():
                raise MCPError(-32602, "conversation_id must be a non-empty string.")
            return self._find_conversation_reference(requested.strip(), required=True)
        if self.config.mcp_default_conversation:
            if not self.config.mcp_default_conversation.strip():
                raise MCPError(-32602, "--mcp-default-conversation must not be blank.")
            return self._find_conversation_reference(
                self.config.mcp_default_conversation,
                required=True,
            )
        if self.config.mcp_default_project:
            if not self.config.mcp_default_project.strip():
                raise MCPError(-32602, "--mcp-default-project must not be blank.")
            conversation = self._find_recent_project_conversation(self.config.mcp_default_project)
            if conversation is not None:
                return conversation
            if required:
                raise MCPError(
                    -32602,
                    f"No cached conversations found for project: {self.config.mcp_default_project}",
                )
        if required:
            raise MCPError(
                -32602,
                "conversation_id is required unless MCP is launched with "
                "--mcp-default-conversation or --mcp-default-project.",
            )
        return None

    def _find_conversation_reference(
        self,
        reference: str,
        *,
        required: bool,
    ) -> Conversation | None:
        reference = reference.strip()
        direct = self.cache.get_conversation(reference)
        if direct is not None:
            return direct
        normalized = reference.casefold()
        conversations = self.cache.list_conversations(pinned_first=False)
        exact_matches = [
            conversation
            for conversation in conversations
            if conversation.title.casefold() == normalized
            or (conversation.chat_identifier or "").casefold() == normalized
            or (conversation.url or "").casefold() == normalized
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]
        partial_matches = [
            conversation
            for conversation in conversations
            if normalized in conversation.title.casefold()
            or normalized in (conversation.url or "").casefold()
        ]
        if len(partial_matches) == 1:
            return partial_matches[0]
        if len(exact_matches) + len(partial_matches) > 1:
            raise MCPError(
                -32602,
                f"Conversation reference is ambiguous: {reference}",
            )
        if required:
            raise MCPError(-32602, f"Conversation not found: {reference}")
        return None

    def _find_recent_project_conversation(self, project_name: str) -> Conversation | None:
        normalized = project_name.strip().casefold()
        for conversation in self.cache.list_conversations(pinned_first=False):
            if (conversation.project_name or "").casefold() == normalized:
                return conversation
        for conversation in self.cache.list_conversations(pinned_first=False):
            if normalized in (conversation.project_name or "").casefold():
                return conversation
        return None

    def _bounded_messages(
        self,
        messages: list[Message],
        arguments: JsonObject,
    ) -> list[Message]:
        limit = self._limit(arguments.get("limit"), default=50, maximum=500)
        offset = self._optional_nonnegative_int(arguments, "offset")
        before_ordinal = self._optional_nonnegative_int(arguments, "before_ordinal")
        if offset is not None and before_ordinal is not None:
            raise MCPError(-32602, "offset and before_ordinal cannot be combined.")
        if before_ordinal is not None:
            return [message for message in messages if message.ordinal < before_ordinal][-limit:]
        if offset is not None:
            return messages[offset : offset + limit]
        return messages[-limit:]

    def _cache_counts(self) -> JsonObject:
        tables = ("projects", "conversations", "messages", "notes", "tags", "bookmarks")
        counts: JsonObject = {}
        for table in tables:
            row = self.cache.connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
            counts[table] = int(row["count"]) if row else 0
        return counts

    def _runtime_lock_status(self) -> JsonObject:
        status = RuntimeLock.status(self.config.runtime_lock_path)
        return {
            "path": str(status.path),
            "exists": status.exists,
            "owner_pid": status.owner_pid,
            "owner_alive": status.owner_alive,
            "locked": status.locked,
            "stale": status.exists and not status.locked and status.owner_alive is False,
        }

    def _package_version(self) -> str:
        return package_version()

    def _require_write_enabled(self) -> None:
        if self.read_only:
            raise MCPError(-32601, "This MCP server was started in read-only mode.")

    async def _live_chatgpt(self) -> ChatGPTService:
        if self.config.offline:
            raise MCPError(-32000, "Live ChatGPT tools require full MCP mode.")
        if self.browser is None:
            self.browser = BrowserController(self.config)
            self.chatgpt = ChatGPTService(self.config, self.browser, self.cache)
        if self.chatgpt is None:
            raise MCPError(-32603, "ChatGPT service failed to initialize.")
        try:
            status = await self.chatgpt.status()
        except Exception:
            await self._park_browser()
            raise
        if status.login_state is not LoginState.LOGGED_IN:
            await self._park_browser()
            diagnostic = status.diagnostic or status.login_state.value
            raise MCPError(
                -32000,
                "ChatGPT browser session is not logged in. "
                f"Open Chatalyst visibly and log in first. Status: {diagnostic}",
            )
        return self.chatgpt

    async def _send_prompt_and_payload(
        self,
        prompt: str,
        *,
        wait_seconds: float,
        project_name: str | None = None,
    ) -> JsonObject:
        if self.chatgpt is None:
            raise MCPError(-32603, "ChatGPT service is unavailable.")
        streamed: list[Message] = []
        send_kwargs: JsonObject = {"response_timeout_seconds": wait_seconds}
        if project_name is not None:
            send_kwargs["project_name"] = project_name
        try:
            async for message in self.chatgpt.send_message(prompt, **send_kwargs):
                streamed.append(message)
        except PromptSubmittedNoAssistantResponseError as exc:
            conversation = self.cache.get_conversation(exc.conversation_id)
            messages, message_count = self._live_result_messages(exc.conversation_id)
            return {
                "status": "submitted_no_response",
                "reason": str(exc),
                "conversation": conversation.model_dump(mode="json") if conversation else None,
                "final_message": None,
                "messages": [message.model_dump(mode="json") for message in messages],
                "message_count": message_count,
                "messages_returned": len(messages),
                "messages_truncated": message_count > len(messages),
                "streamed_message_count": len(streamed),
                "wait_for_response_seconds": wait_seconds,
            }
        final_message = streamed[-1] if streamed else None
        conversation = (
            self.cache.get_conversation(final_message.conversation_id)
            if final_message is not None
            else None
        )
        messages: list[Message] = []
        message_count = 0
        if conversation is not None:
            messages, message_count = self._live_result_messages(conversation.id)
        return {
            "conversation": conversation.model_dump(mode="json") if conversation else None,
            "final_message": final_message.model_dump(mode="json") if final_message else None,
            "messages": [message.model_dump(mode="json") for message in messages],
            "message_count": message_count,
            "messages_returned": len(messages),
            "messages_truncated": message_count > len(messages),
            "streamed_message_count": len(streamed),
            "wait_for_response_seconds": wait_seconds,
        }

    def _live_result_messages(self, conversation_id: str) -> tuple[list[Message], int]:
        messages = self.cache.list_messages(conversation_id)
        message_count = len(messages)
        limit = max(0, self.config.mcp_live_result_message_limit)
        if limit == 0:
            return [], message_count
        return messages[-limit:], message_count

    async def _park_browser(self) -> None:
        if self.browser is not None:
            await self.browser.park_after_work()


async def _run_stdio_async(
    server: ChatalystMCPServer,
    *,
    max_request_bytes: int = 1_000_000,
) -> int:
    try:
        while True:
            line = await _read_stdin_line()
            if line == "":
                break
            if not line.strip():
                continue
            try:
                if len(line.encode("utf-8")) > max_request_bytes:
                    raise MCPError(-32600, "JSON-RPC request exceeds maximum size.")
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise MCPError(-32600, "JSON-RPC request must be an object.")
                response = await server.handle(request)
            except json.JSONDecodeError as exc:
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {exc.msg}"},
                }
            except MCPError as exc:
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": exc.code, "message": exc.message},
                }
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
    finally:
        await server.close()
    return 0


async def _read_stdin_line() -> str:
    return await asyncio.to_thread(sys.stdin.readline)


def run_stdio(server: ChatalystMCPServer, *, max_request_bytes: int = 1_000_000) -> int:
    return asyncio.run(_run_stdio_async(server, max_request_bytes=max_request_bytes))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="chatalyst-mcp",
        description="Run the Chatalyst local-vault MCP server over stdio.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {package_version()}",
        help="Show the installed Chatalyst MCP version and exit.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="Chatalyst workspace containing storage/, exports/, and work/.",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="Expose only read-only MCP tools.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Launch Chromium headless for live tools. Visible is safer for ChatGPT auth.",
    )
    parser.add_argument(
        "--browser-mode",
        choices=("provider", "background", "visible", "headless", "sleep"),
        default="provider",
        help=(
            "Browser lifecycle for live MCP tools. Provider launches a hidden headed "
            "Chromium provider only for live operations, then closes it."
        ),
    )
    parser.add_argument(
        "--browser-profile",
        choices=("standard", "ultralight"),
        default="standard",
        help="Browser resource policy. Ultralight blocks more assets and keeps less DOM visible.",
    )
    parser.add_argument(
        "--max-request-bytes",
        type=int,
        default=1_000_000,
        help="Maximum JSON-RPC request size accepted on stdin.",
    )
    parser.add_argument(
        "--max-text-chars",
        type=int,
        default=100_000,
        help="Maximum text body accepted by write-capable MCP tools.",
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
        default=180.0,
        help="Default MCP live send/reply wait before returning submitted_no_response.",
    )
    parser.add_argument(
        "--mcp-live-result-message-limit",
        type=int,
        default=20,
        help="Recent messages returned by live send/reply tools; full history remains cached.",
    )
    parser.add_argument(
        "--mcp-default-conversation",
        help=(
            "Optional default conversation id, URL, or title used when reply tools "
            "omit conversation_id."
        ),
    )
    parser.add_argument(
        "--mcp-default-project",
        help=(
            "Optional default project name; reply tools use the most recent cached "
            "conversation in that project when conversation_id is omitted."
        ),
    )
    args = parser.parse_args()
    browser_mode = "headless" if args.headless else args.browser_mode
    config = AppConfig.from_workspace(
        args.workspace,
        offline=args.read_only,
        headless=args.headless,
        browser_mode=browser_mode,
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
        read_only=args.read_only,
        max_text_chars=args.max_text_chars,
    )
    raise SystemExit(run_stdio(server, max_request_bytes=args.max_request_bytes))


if __name__ == "__main__":
    main()
