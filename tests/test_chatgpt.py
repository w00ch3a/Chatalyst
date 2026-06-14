from __future__ import annotations

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from chatalyst.core.cache import ChatCache
from chatalyst.core.chatgpt import ChatGPTService, SelectorResolutionError
from chatalyst.core.config import AppConfig
from chatalyst.core.models import Conversation, Message, MessageRole, SyncStatus
from chatalyst.core.selectors import SelectorGroup


class FakeProjectPage:
    url = "https://chatgpt.com/g/project-id"
    project_visible = False
    projects_payload = []

    async def goto(self, url, wait_until=None):
        self.url = url

    async def wait_for_load_state(self, *_args, **_kwargs):
        return None

    async def evaluate(self, _script, _arg=None):
        if "projectish" in _script:
            return self.projects_payload
        if "nav_count" in _script:
            return {"nav_count": 0, "body_text_sample": "", "candidates": []}
        return self.project_visible


class FakeAppLandingPage(FakeProjectPage):
    app_launcher_clicked = False
    required_launch_label: str | None = None

    async def evaluate(self, script, _arg=None):
        if "launchLabels" in script:
            if self.required_launch_label and self.required_launch_label not in script.lower():
                return False
            self.app_launcher_clicked = True
            self.url = "https://chatgpt.com/c/scispace-chat"
            return True
        return await super().evaluate(script, _arg)


class FakeProjectBrowser:
    def __init__(self, page: FakeProjectPage) -> None:
        self.page = page

    async def start(self) -> FakeProjectPage:
        return self.page

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
async def test_project_scoped_new_chat_accepts_project_url_reference(tmp_path):
    config = AppConfig.from_workspace(tmp_path, browser_mode="provider")
    cache = ChatCache(config.database_path)
    cache.initialize()
    page = FakeProjectPage()
    service = ChatGPTService(config, FakeProjectBrowser(page), cache)  # type: ignore[arg-type]
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
        conversation = await service.new_chat(project_name="https://chatgpt.com/g/project-alpha")
    finally:
        cache.close()

    assert clicked_new_chat is False
    assert page.url == "https://chatgpt.com/g/project-alpha"
    assert conversation.project_id == "project-alpha"
    assert conversation.project_name == "https://chatgpt.com/g/project-alpha"


@pytest.mark.asyncio
async def test_project_scoped_new_chat_accepts_chatgpt_app_url_reference(tmp_path):
    config = AppConfig.from_workspace(tmp_path, browser_mode="provider")
    cache = ChatCache(config.database_path)
    cache.initialize()
    page = FakeProjectPage()
    service = ChatGPTService(config, FakeProjectBrowser(page), cache)  # type: ignore[arg-type]
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
        conversation = await service.new_chat(project_name="https://chatgpt.com/apps/scispace")
    finally:
        cache.close()

    assert clicked_new_chat is False
    assert page.url == "https://chatgpt.com/apps/scispace"
    assert conversation.project_id == service._hash_id("https://chatgpt.com/apps/scispace")  # noqa: SLF001
    assert conversation.project_name == "https://chatgpt.com/apps/scispace"


@pytest.mark.asyncio
async def test_project_scoped_new_chat_launches_chatgpt_app_landing_page(tmp_path):
    config = AppConfig.from_workspace(tmp_path, browser_mode="provider")
    cache = ChatCache(config.database_path)
    cache.initialize()
    page = FakeAppLandingPage()
    service = ChatGPTService(config, FakeProjectBrowser(page), cache)  # type: ignore[arg-type]
    composer_checks = 0

    async def any_visible(_page, _selector_group):
        nonlocal composer_checks
        composer_checks += 1
        return composer_checks > 1

    async def wait_for_composer(_page, _selector_group):
        return None

    service._any_visible = any_visible  # type: ignore[method-assign]
    service._wait_for_any = wait_for_composer  # type: ignore[method-assign]
    try:
        conversation = await service.new_chat(project_name="https://chatgpt.com/apps/scispace")
    finally:
        cache.close()

    assert page.app_launcher_clicked is True
    assert page.url == "https://chatgpt.com/c/scispace-chat"
    assert conversation.project_id == service._hash_id("https://chatgpt.com/apps/scispace")  # noqa: SLF001
    assert conversation.project_name == "https://chatgpt.com/apps/scispace"


@pytest.mark.asyncio
async def test_project_scoped_new_chat_launches_app_with_ask_label(tmp_path):
    config = AppConfig.from_workspace(tmp_path, browser_mode="provider")
    cache = ChatCache(config.database_path)
    cache.initialize()
    page = FakeAppLandingPage()
    page.required_launch_label = "ask"
    service = ChatGPTService(config, FakeProjectBrowser(page), cache)  # type: ignore[arg-type]

    async def any_visible(_page, _selector_group):
        return False

    async def wait_for_composer(_page, _selector_group):
        return None

    service._any_visible = any_visible  # type: ignore[method-assign]
    service._wait_for_any = wait_for_composer  # type: ignore[method-assign]
    try:
        conversation = await service.new_chat(project_name="https://chatgpt.com/apps/scispace")
    finally:
        cache.close()

    assert page.app_launcher_clicked is True
    assert page.url == "https://chatgpt.com/c/scispace-chat"
    assert conversation.project_id == service._hash_id("https://chatgpt.com/apps/scispace")  # noqa: SLF001


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
async def test_extract_projects_reads_visible_project_links(tmp_path):
    config = AppConfig.from_workspace(tmp_path, browser_mode="provider")
    cache = ChatCache(config.database_path)
    cache.initialize()
    page = FakeProjectPage()
    page.projects_payload = [
        {"href": "https://chatgpt.com/g/project-alpha", "name": "Alpha"},
        {"href": "https://chatgpt.com/g/project-alpha", "name": "Alpha"},
        {"href": "", "name": "Loose Project"},
    ]
    service = ChatGPTService(config, FakeProjectBrowser(page), cache)  # type: ignore[arg-type]
    try:
        projects = await service.extract_projects(page)
    finally:
        cache.close()

    assert [(project.id, project.name) for project in projects] == [
        ("project-alpha", "Alpha"),
        (service._hash_id("Loose Project"), "Loose Project"),  # noqa: SLF001
    ]


@pytest.mark.asyncio
async def test_extract_projects_reads_project_rows_without_links(tmp_path):
    config = AppConfig.from_workspace(tmp_path, browser_mode="provider")
    cache = ChatCache(config.database_path)
    cache.initialize()
    page = FakeProjectPage()
    page.projects_payload = [
        {"href": "", "name": "Work", "aria": "Project Work", "testid": "", "isProject": True},
    ]
    service = ChatGPTService(config, FakeProjectBrowser(page), cache)  # type: ignore[arg-type]
    try:
        projects = await service.extract_projects(page)
    finally:
        cache.close()

    assert len(projects) == 1
    assert projects[0].name == "Work"
    assert projects[0].url is None


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


@pytest.mark.asyncio
async def test_project_scope_verifies_only_when_browser_and_cache_match(tmp_path):
    config = AppConfig.from_workspace(tmp_path, browser_mode="provider")
    cache = ChatCache(config.database_path)
    cache.initialize()
    page = FakeProjectPage()
    page.url = "https://chatgpt.com/c/chat-1"
    page.project_visible = True
    cache.upsert_conversation(
        Conversation(
            id="chat-1",
            title="Project Chat",
            project_name="Research",
            sync_status=SyncStatus.CACHED,
        )
    )
    service = ChatGPTService(config, FakeProjectBrowser(page), cache)  # type: ignore[arg-type]
    try:
        scope = await service.verify_project_scope("Research")
    finally:
        cache.close()

    assert scope.verified is True
    assert scope.reason == "visible_or_url_project_and_cache_match"
    assert scope.url == "https://chatgpt.com/c/chat-1"


@pytest.mark.asyncio
async def test_project_scope_verifies_url_reference_by_cached_project_id(tmp_path):
    config = AppConfig.from_workspace(tmp_path, browser_mode="provider")
    cache = ChatCache(config.database_path)
    cache.initialize()
    page = FakeProjectPage()
    page.url = "https://chatgpt.com/g/project-alpha"
    page.project_visible = False
    cache.upsert_conversation(
        Conversation(
            id=(service_id := "local-project-url-chat"),
            title="Project Chat",
            project_id="project-alpha",
            project_name="Alpha",
            sync_status=SyncStatus.CACHED,
        )
    )
    service = ChatGPTService(config, FakeProjectBrowser(page), cache)  # type: ignore[arg-type]
    service._conversation_id_from_url = lambda _url: service_id  # type: ignore[method-assign]
    try:
        scope = await service.verify_project_scope("https://chatgpt.com/g/project-alpha")
    finally:
        cache.close()

    assert scope.verified is True
    assert scope.reason == "visible_or_url_project_and_cache_match"


@pytest.mark.asyncio
async def test_project_scope_marks_cache_only_match_as_uncertain(tmp_path):
    config = AppConfig.from_workspace(tmp_path, browser_mode="provider")
    cache = ChatCache(config.database_path)
    cache.initialize()
    page = FakeProjectPage()
    page.url = "https://chatgpt.com/c/chat-1"
    page.project_visible = False
    cache.upsert_conversation(
        Conversation(
            id="chat-1",
            title="Project Chat",
            project_name="Research",
            sync_status=SyncStatus.CACHED,
        )
    )
    service = ChatGPTService(config, FakeProjectBrowser(page), cache)  # type: ignore[arg-type]
    try:
        scope = await service.verify_project_scope("Research")
    finally:
        cache.close()

    assert scope.verified is False
    assert scope.reason == "cache_match_only"


@pytest.mark.asyncio
async def test_project_scope_verifies_chatgpt_app_url_by_cached_conversation(tmp_path):
    config = AppConfig.from_workspace(tmp_path, browser_mode="provider")
    cache = ChatCache(config.database_path)
    cache.initialize()
    app_url = "https://chatgpt.com/apps/scispace"
    page = FakeProjectPage()
    page.url = "https://chatgpt.com/c/chat-1"
    page.project_visible = False
    service = ChatGPTService(config, FakeProjectBrowser(page), cache)  # type: ignore[arg-type]
    cache.upsert_conversation(
        Conversation(
            id="chat-1",
            title="SciSpace Chat",
            project_id=service._hash_id(app_url),  # noqa: SLF001
            project_name=app_url,
            sync_status=SyncStatus.CACHED,
        )
    )
    try:
        scope = await service.verify_project_scope(app_url)
    finally:
        cache.close()

    assert scope.verified is True
    assert scope.reason == "app_conversation_and_cache_match"


class FailingLocator:
    async def wait_for(self, *, timeout):
        raise PlaywrightTimeoutError("selector missing")


class FailingLocatorHandle:
    @property
    def first(self):
        return FailingLocator()


class DiagnosticPage(FakeProjectPage):
    url = "https://chatgpt.com/"

    def locator(self, _selector):
        return FailingLocatorHandle()

    async def evaluate(self, _script, _arg=None):
        return "Visible ChatGPT text"

    async def title(self):
        return "ChatGPT"

    async def screenshot(self, *, path, full_page=False):
        with open(path, "wb") as handle:
            handle.write(b"fake-png")


@pytest.mark.asyncio
async def test_selector_failure_writes_private_diagnostic_pack(tmp_path):
    config = AppConfig.from_workspace(tmp_path, browser_mode="provider")
    config.selector_timeout_ms = 1
    cache = ChatCache(config.database_path)
    cache.initialize()
    page = DiagnosticPage()
    service = ChatGPTService(config, FakeProjectBrowser(page), cache)  # type: ignore[arg-type]
    group = SelectorGroup("composer", ("[data-test='missing']",), "missing selector")

    try:
        with pytest.raises(SelectorResolutionError):
            await service._locator(page, group)  # noqa: SLF001
    finally:
        cache.close()

    packs = list(config.logs_dir.glob("selector-failure-*"))
    assert len(packs) == 1
    pack = packs[0]
    assert oct(pack.stat().st_mode & 0o777) == "0o700"
    assert (pack / "url.txt").read_text(encoding="utf-8") == "https://chatgpt.com/"
    assert (pack / "title.txt").read_text(encoding="utf-8") == "ChatGPT"
    assert "Visible ChatGPT text" in (pack / "visible-text-sample.txt").read_text(
        encoding="utf-8"
    )
    assert (pack / "screenshot.png").read_bytes() == b"fake-png"
    for path in pack.iterdir():
        assert oct(path.stat().st_mode & 0o777) == "0o600"
