from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from chatalyst.core.chatgpt import PromptSubmittedNoAssistantResponseError
from chatalyst.core.config import AppConfig
from chatalyst.core.mcp_server import ChatalystMCPServer, MCPError, _read_stdin_line
from chatalyst.core.models import Conversation, Message, MessageRole, Project, SyncStatus
from chatalyst.core.runtime import RuntimeLock


def _server(tmp_path, *, wait_seconds: float = 75.0) -> ChatalystMCPServer:
    config = AppConfig.from_workspace(tmp_path, browser_mode="provider").model_copy(
        update={
            "mcp_live_response_timeout_seconds": wait_seconds,
            "mcp_live_result_message_limit": 20,
            "live_tool_lock_timeout_seconds": 0.05,
        }
    )
    return ChatalystMCPServer(config)


def test_mcp_wait_seconds_defaults_to_config(tmp_path):
    server = _server(tmp_path, wait_seconds=42.0)
    try:
        assert server._optional_wait_seconds({}) == 42.0
    finally:
        server.cache.close()


def test_mcp_wait_seconds_accepts_valid_override(tmp_path):
    server = _server(tmp_path)
    try:
        assert server._optional_wait_seconds({"wait_for_response_seconds": 900}) == 900.0
    finally:
        server.cache.close()


@pytest.mark.parametrize("value", [4, 901, "75"])
def test_mcp_wait_seconds_rejects_invalid_override(tmp_path, value):
    server = _server(tmp_path)
    try:
        with pytest.raises(MCPError):
            server._optional_wait_seconds({"wait_for_response_seconds": value})
    finally:
        server.cache.close()


def test_mcp_wait_seconds_rejects_bool_override(tmp_path):
    server = _server(tmp_path)
    try:
        with pytest.raises(MCPError):
            server._optional_wait_seconds({"wait_for_response_seconds": True})
    finally:
        server.cache.close()


def test_mcp_project_name_defaults_to_config(tmp_path):
    server = _server(tmp_path)
    server.config = server.config.model_copy(update={"mcp_default_project": "Research"})
    try:
        assert server._optional_project_name({}) == "Research"
    finally:
        server.cache.close()


def test_mcp_project_name_argument_overrides_config(tmp_path):
    server = _server(tmp_path)
    server.config = server.config.model_copy(update={"mcp_default_project": "Research"})
    try:
        assert server._optional_project_name({"project_name": "Ops"}) == "Ops"
    finally:
        server.cache.close()


def test_mcp_project_name_rejects_blank_value(tmp_path):
    server = _server(tmp_path)
    try:
        with pytest.raises(MCPError, match="must not be blank"):
            server._optional_project_name({"project_name": "   "})
    finally:
        server.cache.close()


def test_mcp_limit_rejects_bool(tmp_path):
    server = _server(tmp_path)
    try:
        with pytest.raises(MCPError):
            server._limit(True, default=20, maximum=100)
    finally:
        server.cache.close()


def test_mcp_scope_resolves_default_conversation_title(tmp_path):
    server = _server(tmp_path)
    conversation = Conversation(
        id="chat-1",
        title="Release Planning Report",
        sync_status=SyncStatus.CACHED,
    )
    server.cache.upsert_conversation(conversation)
    server.config = server.config.model_copy(
        update={"mcp_default_conversation": "Planning Report"}
    )
    try:
        scoped = server._resolve_scoped_conversation(required=True)
        scope_payload = server._tool_get_scope({})
    finally:
        server.cache.close()

    assert scoped is not None
    assert scoped.id == conversation.id
    assert scope_payload["resolved_conversation"]["id"] == conversation.id


def test_mcp_scope_resolves_default_project_to_recent_conversation(tmp_path):
    server = _server(tmp_path)
    old = Conversation(
        id="old-chat",
        title="Old Project Chat",
        project_name="Research",
        sync_status=SyncStatus.CACHED,
    )
    new = Conversation(
        id="new-chat",
        title="New Project Chat",
        project_name="Research",
        sync_status=SyncStatus.CACHED,
    )
    server.cache.upsert_conversation(old)
    server.cache.upsert_conversation(new)
    server.cache.mark_opened(new.id)
    server.config = server.config.model_copy(update={"mcp_default_project": "research"})
    try:
        scoped = server._resolve_scoped_conversation(required=True)
    finally:
        server.cache.close()

    assert scoped is not None
    assert scoped.id == new.id


def test_mcp_scope_rejects_blank_default_conversation(tmp_path):
    server = _server(tmp_path)
    server.config = server.config.model_copy(update={"mcp_default_conversation": "   "})
    try:
        with pytest.raises(MCPError, match="must not be blank"):
            server._resolve_scoped_conversation(required=True)
    finally:
        server.cache.close()


def test_mcp_lists_cached_projects(tmp_path):
    server = _server(tmp_path)
    server.cache.upsert_project(Project(id="project-1", name="Research"))
    try:
        payload = server._tool_list_projects({})
    finally:
        server.cache.close()

    assert payload["count"] == 1
    assert payload["projects"][0]["name"] == "Research"


@pytest.mark.asyncio
async def test_mcp_health_reports_scope_and_cache_counts(tmp_path):
    server = _server(tmp_path)
    conversation = Conversation(
        id="chat-1",
        title="Health Chat",
        project_name="Research",
        sync_status=SyncStatus.CACHED,
    )
    server.cache.upsert_conversation(conversation)
    server.config = server.config.model_copy(update={"mcp_default_project": "Research"})
    try:
        payload = await server._tool_health({})
    finally:
        server.cache.close()

    assert payload["workspace"] == str(tmp_path)
    assert payload["browser"]["checked"] is False
    assert payload["default_project"] == "Research"
    assert payload["default_project_has_cached_conversation"] is True
    assert payload["runtime_lock"]["exists"] is False
    assert payload["cache_counts"]["conversations"] == 1


def test_mcp_get_conversation_returns_recent_messages_by_default(tmp_path):
    server = _server(tmp_path)
    conversation = Conversation(
        id="chat-1",
        title="Long Chat",
        sync_status=SyncStatus.CACHED,
    )
    server.cache.upsert_conversation(conversation)
    for ordinal in range(60):
        server.cache.upsert_message(
            Message(
                id=f"msg-{ordinal}",
                conversation_id=conversation.id,
                role=MessageRole.USER,
                markdown=f"message {ordinal}",
                ordinal=ordinal,
            )
        )
    try:
        payload = server._tool_get_conversation({"conversation_id": conversation.id})
    finally:
        server.cache.close()

    assert payload["message_count"] == 60
    assert payload["messages_returned"] == 50
    assert payload["messages_truncated"] is True
    assert payload["messages"][0]["markdown"] == "message 10"
    assert payload["messages"][-1]["markdown"] == "message 59"


def test_mcp_get_conversation_supports_offset_and_no_messages(tmp_path):
    server = _server(tmp_path)
    conversation = Conversation(
        id="chat-1",
        title="Windowed Chat",
        sync_status=SyncStatus.CACHED,
    )
    server.cache.upsert_conversation(conversation)
    for ordinal in range(5):
        server.cache.upsert_message(
            Message(
                id=f"msg-{ordinal}",
                conversation_id=conversation.id,
                role=MessageRole.USER,
                markdown=f"message {ordinal}",
                ordinal=ordinal,
            )
        )
    try:
        window = server._tool_get_conversation(
            {"conversation_id": conversation.id, "offset": 1, "limit": 2}
        )
        metadata_only = server._tool_get_conversation(
            {"conversation_id": conversation.id, "include_messages": False}
        )
    finally:
        server.cache.close()

    assert [message["markdown"] for message in window["messages"]] == [
        "message 1",
        "message 2",
    ]
    assert metadata_only["messages"] == []
    assert metadata_only["message_count"] == 5
    assert metadata_only["messages_returned"] == 0
    assert metadata_only["messages_truncated"] is False


@pytest.mark.asyncio
async def test_mcp_internal_errors_are_sanitized(tmp_path):
    server = _server(tmp_path)

    async def explode(_method, _params):
        raise RuntimeError(f"private path: {tmp_path / 'storage' / 'chat_cache.db'}")

    server._dispatch = explode  # type: ignore[method-assign]
    try:
        response = await server.handle({"jsonrpc": "2.0", "id": 1, "method": "boom"})
    finally:
        server.cache.close()

    assert response is not None
    assert response["error"]["code"] == -32603
    assert response["error"]["message"] == "Internal error."
    assert str(tmp_path) not in response["error"]["message"]


@pytest.mark.asyncio
async def test_mcp_handle_responds_to_zero_request_id(tmp_path):
    server = _server(tmp_path)
    try:
        response = await server.handle({"jsonrpc": "2.0", "id": 0, "method": "ping"})
    finally:
        server.cache.close()

    assert response == {"jsonrpc": "2.0", "id": 0, "result": {}}


@pytest.mark.asyncio
async def test_mcp_handle_ignores_notification_without_response(tmp_path):
    server = _server(tmp_path)
    try:
        response = await server.handle(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        )
    finally:
        server.cache.close()

    assert response is None


@pytest.mark.asyncio
async def test_mcp_initialize_reports_package_version(tmp_path):
    server = _server(tmp_path)
    try:
        response = await server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    finally:
        server.cache.close()

    assert response is not None
    assert response["result"]["serverInfo"] == {
        "name": "chatalyst",
        "version": server._package_version(),  # noqa: SLF001
    }


@pytest.mark.asyncio
async def test_mcp_send_payload_reports_submitted_without_response(tmp_path):
    server = _server(tmp_path)
    conversation = Conversation(
        id="chat-1",
        title="Quiet Chat",
        sync_status=SyncStatus.CACHED,
    )
    user_message = Message(
        id="msg-1",
        conversation_id=conversation.id,
        role=MessageRole.USER,
        markdown="hello",
    )
    server.cache.upsert_conversation(conversation)
    server.cache.upsert_message(user_message)

    class QuietChatGPT:
        async def send_message(self, prompt, *, response_timeout_seconds=None):
            raise PromptSubmittedNoAssistantResponseError(conversation.id, prompt)
            yield  # pragma: no cover

    server.chatgpt = QuietChatGPT()  # type: ignore[assignment]
    try:
        payload = await server._send_prompt_and_payload("hello", wait_seconds=12)
    finally:
        server.cache.close()

    assert payload["status"] == "submitted_no_response"
    assert payload["conversation"]["id"] == conversation.id
    assert payload["final_message"] is None
    assert payload["messages"][0]["markdown"] == "hello"
    assert payload["message_count"] == 1
    assert payload["messages_returned"] == 1
    assert payload["messages_truncated"] is False
    assert payload["wait_for_response_seconds"] == 12


@pytest.mark.asyncio
async def test_mcp_live_send_payload_returns_bounded_recent_messages(tmp_path):
    server = _server(tmp_path)
    conversation = Conversation(
        id="chat-1",
        title="Long Chat",
        sync_status=SyncStatus.CACHED,
    )
    server.cache.upsert_conversation(conversation)
    for ordinal in range(30):
        server.cache.upsert_message(
            Message(
                id=f"msg-{ordinal}",
                conversation_id=conversation.id,
                role=MessageRole.USER if ordinal % 2 == 0 else MessageRole.ASSISTANT,
                markdown=f"message {ordinal}",
                ordinal=ordinal,
            )
        )

    class RespondingChatGPT:
        async def send_message(self, prompt, *, response_timeout_seconds=None):
            yield Message(
                id="msg-30",
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                markdown="message 30",
                ordinal=30,
            )

    server.chatgpt = RespondingChatGPT()  # type: ignore[assignment]
    try:
        payload = await server._send_prompt_and_payload("hello", wait_seconds=12)
    finally:
        server.cache.close()

    assert payload["message_count"] == 30
    assert payload["messages_returned"] == 20
    assert payload["messages_truncated"] is True
    assert payload["messages"][0]["markdown"] == "message 10"
    assert payload["messages"][-1]["markdown"] == "message 29"
    assert payload["final_message"]["markdown"] == "message 30"


@pytest.mark.asyncio
async def test_mcp_reply_uses_default_conversation_when_argument_omitted(tmp_path):
    server = _server(tmp_path)
    conversation = Conversation(
        id="chat-1",
        title="Default Chat",
        url="https://chatgpt.com/c/chat-1",
        sync_status=SyncStatus.CACHED,
    )
    server.cache.upsert_conversation(conversation)
    server.config = server.config.model_copy(update={"mcp_default_conversation": "Default Chat"})

    class ScopedChatGPT:
        opened_conversation_id: str | None = None

        async def open_conversation(self, conversation_id):
            self.opened_conversation_id = conversation_id
            return SimpleNamespace(conversation=conversation)

        async def send_message(self, prompt, *, response_timeout_seconds=None):
            yield Message(
                id="msg-1",
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                markdown="ok",
                ordinal=1,
            )

    chatgpt = ScopedChatGPT()

    async def live_chatgpt():
        server.chatgpt = chatgpt  # type: ignore[assignment]
        return chatgpt

    async def park_browser():
        return None

    server._live_chatgpt = live_chatgpt  # type: ignore[method-assign]
    server._park_browser = park_browser  # type: ignore[method-assign]
    try:
        payload = await server._tool_reply_to_conversation({"prompt": "hello"})
    finally:
        server.cache.close()

    assert chatgpt.opened_conversation_id == conversation.id
    assert payload["conversation"]["id"] == conversation.id
    assert payload["final_message"]["markdown"] == "ok"


@pytest.mark.asyncio
async def test_mcp_send_new_message_uses_default_project(tmp_path):
    server = _server(tmp_path)
    conversation = Conversation(
        id="chat-1",
        title="Project Chat",
        project_name="Research",
        sync_status=SyncStatus.CACHED,
    )
    server.cache.upsert_conversation(conversation)
    server.config = server.config.model_copy(update={"mcp_default_project": "Research"})

    class ProjectChatGPT:
        new_chat_project_name: str | None = None
        send_project_name: str | None = None

        async def new_chat(self, *, project_name=None):
            self.new_chat_project_name = project_name
            return conversation

        async def send_message(self, prompt, *, response_timeout_seconds=None, project_name=None):
            self.send_project_name = project_name
            yield Message(
                id="msg-1",
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                markdown="ok",
                ordinal=1,
            )

        async def verify_project_scope(self, project_name):
            return SimpleNamespace(
                requested_project=project_name,
                verified=True,
                reason="visible_project_and_cache_match",
                url="https://chatgpt.com/c/chat-1",
            )

    chatgpt = ProjectChatGPT()

    async def live_chatgpt():
        server.chatgpt = chatgpt  # type: ignore[assignment]
        return chatgpt

    async def park_browser():
        return None

    server._live_chatgpt = live_chatgpt  # type: ignore[method-assign]
    server._park_browser = park_browser  # type: ignore[method-assign]
    try:
        payload = await server._tool_send_new_message({"prompt": "hello"})
    finally:
        server.cache.close()

    assert chatgpt.new_chat_project_name == "Research"
    assert chatgpt.send_project_name == "Research"
    assert payload["final_message"]["markdown"] == "ok"


@pytest.mark.asyncio
async def test_mcp_send_new_message_allows_project_override(tmp_path):
    server = _server(tmp_path)
    conversation = Conversation(
        id="chat-1",
        title="Project Chat",
        project_name="Ops",
        sync_status=SyncStatus.CACHED,
    )
    server.cache.upsert_conversation(conversation)
    server.config = server.config.model_copy(update={"mcp_default_project": "Research"})

    class ProjectChatGPT:
        new_chat_project_name: str | None = None

        async def new_chat(self, *, project_name=None):
            self.new_chat_project_name = project_name
            return conversation

        async def send_message(self, prompt, *, response_timeout_seconds=None, project_name=None):
            yield Message(
                id="msg-1",
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                markdown="ok",
                ordinal=1,
            )

        async def verify_project_scope(self, project_name):
            return SimpleNamespace(
                requested_project=project_name,
                verified=True,
                reason="visible_project_and_cache_match",
                url="https://chatgpt.com/c/chat-1",
            )

    chatgpt = ProjectChatGPT()

    async def live_chatgpt():
        server.chatgpt = chatgpt  # type: ignore[assignment]
        return chatgpt

    async def park_browser():
        return None

    server._live_chatgpt = live_chatgpt  # type: ignore[method-assign]
    server._park_browser = park_browser  # type: ignore[method-assign]
    try:
        await server._tool_send_new_message({"prompt": "hello", "project_name": "Ops"})
    finally:
        server.cache.close()

    assert chatgpt.new_chat_project_name == "Ops"


@pytest.mark.asyncio
async def test_mcp_send_new_message_reports_uncertain_project_scope(tmp_path):
    server = _server(tmp_path)
    conversation = Conversation(
        id="chat-1",
        title="Project Chat",
        project_name="Research",
        sync_status=SyncStatus.CACHED,
    )
    server.cache.upsert_conversation(conversation)
    server.config = server.config.model_copy(update={"mcp_default_project": "Research"})

    class ProjectChatGPT:
        async def new_chat(self, *, project_name=None):
            return conversation

        async def send_message(self, prompt, *, response_timeout_seconds=None, project_name=None):
            yield Message(
                id="msg-1",
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                markdown="ok",
                ordinal=1,
            )

        async def verify_project_scope(self, project_name):
            return SimpleNamespace(
                requested_project=project_name,
                verified=False,
                reason="cache_match_only",
                url="https://chatgpt.com/c/chat-1",
            )

    chatgpt = ProjectChatGPT()

    async def live_chatgpt():
        server.chatgpt = chatgpt  # type: ignore[assignment]
        return chatgpt

    async def park_browser():
        return None

    server._live_chatgpt = live_chatgpt  # type: ignore[method-assign]
    server._park_browser = park_browser  # type: ignore[method-assign]
    try:
        payload = await server._tool_send_new_message({"prompt": "hello"})
    finally:
        server.cache.close()

    assert payload["status"] == "scope_uncertain"
    assert payload["scope"] == {
        "requested_project": "Research",
        "verified": False,
        "reason": "cache_match_only",
        "url": "https://chatgpt.com/c/chat-1",
    }


@pytest.mark.asyncio
async def test_mcp_live_tool_lock_contention_returns_tool_error(tmp_path):
    server = _server(tmp_path)
    lock = RuntimeLock(server.config.runtime_lock_path)
    lock.acquire()
    try:
        with pytest.raises(MCPError, match="browser lane is busy"):
            await server._tool_reply_to_conversation(
                {"conversation_id": "chat-1", "prompt": "hello"}
            )
    finally:
        lock.release()
        server.cache.close()


@pytest.mark.asyncio
async def test_mcp_stdin_read_does_not_block_event_loop(monkeypatch):
    class SlowStdin:
        def readline(self) -> str:
            time.sleep(0.05)
            return ""

    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.01)

    monkeypatch.setattr("sys.stdin", SlowStdin())
    ticker_task = asyncio.create_task(ticker())
    try:
        assert await _read_stdin_line() == ""
        assert ticks > 1
    finally:
        ticker_task.cancel()
