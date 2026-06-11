from __future__ import annotations

import pytest

from chatalyst.core.cache import ChatCache
from chatalyst.core.chatgpt import ChatGPTService
from chatalyst.core.config import AppConfig
from chatalyst.core.models import Conversation, Message, MessageRole, SyncStatus


class FakeProjectPage:
    url = "https://chatgpt.com/g/project-id"


class FakeProjectBrowser:
    def __init__(self, page: FakeProjectPage) -> None:
        self.page = page

    async def open_chatgpt(self) -> FakeProjectPage:
        return self.page


@pytest.mark.asyncio
async def test_project_scoped_new_chat_does_not_click_global_new_chat(tmp_path):
    config = AppConfig.from_workspace(tmp_path, browser_mode="provider")
    cache = ChatCache(config.database_path)
    cache.initialize()
    service = ChatGPTService(config, FakeProjectBrowser(FakeProjectPage()), cache)  # type: ignore[arg-type]
    opened_projects: list[str] = []
    clicked_new_chat = False

    async def open_project(_page, project_name):
        opened_projects.append(project_name)

    async def click_new_chat(_page, _selector_group):
        nonlocal clicked_new_chat
        clicked_new_chat = True
        return True

    async def wait_for_composer(_page, _selector_group):
        return None

    service._open_project = open_project  # type: ignore[method-assign]
    service._click_first = click_new_chat  # type: ignore[method-assign]
    service._wait_for_any = wait_for_composer  # type: ignore[method-assign]
    try:
        conversation = await service.new_chat(project_name="Research")
    finally:
        cache.close()

    assert opened_projects == ["Research"]
    assert clicked_new_chat is False
    assert conversation.project_name == "Research"


@pytest.mark.asyncio
async def test_unscoped_new_chat_uses_global_new_chat(tmp_path):
    config = AppConfig.from_workspace(tmp_path, browser_mode="provider")
    cache = ChatCache(config.database_path)
    cache.initialize()
    service = ChatGPTService(config, FakeProjectBrowser(FakeProjectPage()), cache)  # type: ignore[arg-type]
    clicked_new_chat = False

    async def click_new_chat(_page, _selector_group):
        nonlocal clicked_new_chat
        clicked_new_chat = True
        return True

    async def wait_for_composer(_page, _selector_group):
        return None

    service._click_first = click_new_chat  # type: ignore[method-assign]
    service._wait_for_any = wait_for_composer  # type: ignore[method-assign]
    try:
        conversation = await service.new_chat()
    finally:
        cache.close()

    assert clicked_new_chat is True
    assert conversation.project_name is None


@pytest.mark.asyncio
async def test_project_name_survives_send_message_url_reconciliation(tmp_path):
    config = AppConfig.from_workspace(tmp_path, browser_mode="provider")
    config.response_poll_interval_seconds = 0.01
    config.max_stream_idle_seconds = 0.01
    cache = ChatCache(config.database_path)
    cache.initialize()
    page = FakeProjectPage()
    service = ChatGPTService(config, FakeProjectBrowser(page), cache)  # type: ignore[arg-type]
    original_id = service._conversation_id_from_url(page.url)
    cache.upsert_conversation(
        Conversation(
            id=original_id,
            title="New Chat",
            url=page.url,
            chat_identifier=original_id,
            project_name="Research",
            sync_status=SyncStatus.SYNCING,
        )
    )

    async def wait_until_idle(_page):
        return None

    async def extract_messages(_page, conversation_id):
        if _page.url == "https://chatgpt.com/g/project-id":
            return []
        return [
            Message(
                id="user-message",
                conversation_id=conversation_id,
                role=MessageRole.USER,
                markdown="Hello",
                ordinal=0,
            ),
            Message(
                id="assistant-message",
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                markdown="Hi",
                ordinal=1,
            ),
        ]

    async def locator(_page, _selector_group):
        class Composer:
            async def press(self, _key):
                return None

        return Composer()

    async def fill_composer(_page, _composer, _prompt):
        return None

    async def click_first(_page, _selector_group):
        _page.url = "https://chatgpt.com/c/reconciled-chat-id"
        return True

    async def any_visible(_page, _selector_group):
        return False

    async def extract_title(_page, _fallback):
        return "Reconciled Chat"

    async def apply_display_pruning(_page):
        return None

    service._wait_until_chatgpt_idle = wait_until_idle  # type: ignore[method-assign]
    service.extract_messages = extract_messages  # type: ignore[method-assign]
    service._locator = locator  # type: ignore[method-assign]
    service._fill_composer = fill_composer  # type: ignore[method-assign]
    service._click_first = click_first  # type: ignore[method-assign]
    service._any_visible = any_visible  # type: ignore[method-assign]
    service._extract_title = extract_title  # type: ignore[method-assign]
    service.apply_display_pruning = apply_display_pruning  # type: ignore[method-assign]
    try:
        messages = [message async for message in service.send_message("Hello")]
        reconciled = cache.get_conversation("reconciled-chat-id")
    finally:
        cache.close()

    assert [message.markdown for message in messages] == ["Hi", "Hi"]
    assert reconciled is not None
    assert reconciled.project_name == "Research"
