from __future__ import annotations

import pytest

from chatalyst.core.cache import ChatCache
from chatalyst.core.chatgpt import ChatGPTService
from chatalyst.core.config import AppConfig


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
