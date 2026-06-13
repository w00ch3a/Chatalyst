from __future__ import annotations

import stat

import pytest

from chatalyst.core.browser import (
    BrowserController,
    BrowserLaunchPlanner,
    BrowserOptimizationPolicy,
)
from chatalyst.core.config import AppConfig
from chatalyst.core.models import BrowserState


class FakePage:
    def __init__(self, *, closed: bool) -> None:
        self._closed = closed

    def is_closed(self) -> bool:
        return self._closed


class FakeContext:
    def __init__(self, pages: list[FakePage]) -> None:
        self.pages = pages

    async def new_page(self) -> FakePage:
        page = FakePage(closed=False)
        self.pages.append(page)
        return page


class FakeStorageContext:
    def __init__(self) -> None:
        self.pages = []

    async def storage_state(self, *, path: str) -> None:
        with open(path, "w", encoding="utf-8") as file:
            file.write("{}")


@pytest.mark.asyncio
async def test_browser_start_replaces_closed_cached_page(tmp_path):
    config = AppConfig.from_workspace(tmp_path, browser_mode="provider")
    browser = BrowserController(config)
    closed_page = FakePage(closed=True)
    live_page = FakePage(closed=False)
    browser._page = closed_page  # type: ignore[assignment]
    browser._context = FakeContext([closed_page, live_page])  # type: ignore[assignment]

    page = await browser.start()

    assert page is live_page
    assert browser._page is live_page
    assert browser.status.browser_state is BrowserState.READY


def test_browser_launch_planner_respects_forced_headless():
    config = AppConfig.from_workspace(".", browser_mode="provider", headless=True)
    planner = BrowserLaunchPlanner(config.browser_mode)

    assert planner.headless_for_start(None, force_headless=config.headless) is True
    assert planner.headless_for_start(True, force_headless=config.headless) is False


def test_ultralight_browser_profile_tightens_launch_and_pruning():
    config = AppConfig.from_workspace(".", browser_mode="provider", browser_profile="ultralight")
    policy = BrowserOptimizationPolicy.from_config(config)

    assert config.browser_viewport_width == 1000
    assert config.browser_viewport_height == 720
    assert config.browser_retain_recent_turns == 4
    assert config.browser_retain_sidebar_items == 20
    assert policy.should_abort_request("https://chatgpt.com/assets/app.css", "stylesheet")
    assert policy.should_abort_request("https://example.com/", "document")
    assert not policy.should_abort_request("https://chatgpt.com/", "document")
    assert not policy.should_abort_request("https://auth.openai.com/login", "document")
    assert "--disable-gpu" in policy.chromium_args()
    assert "--process-per-site" in policy.chromium_args()
    assert "--renderer-process-limit=2" in policy.chromium_args()
    assert "--hide-scrollbars" in policy.chromium_args()
    assert "--no-pings" in policy.chromium_args()
    assert "--disable-component-extensions-with-background-pages" in policy.chromium_args()
    assert any("Prerender2" in arg for arg in policy.chromium_args())


def test_standard_browser_profile_keeps_stylesheets_available():
    config = AppConfig.from_workspace(".", browser_mode="provider", browser_profile="standard")
    policy = BrowserOptimizationPolicy.from_config(config)

    assert config.browser_viewport_width == 1440
    assert config.browser_retain_recent_turns == 12
    assert not policy.should_abort_request("https://chatgpt.com/assets/app.css", "stylesheet")
    assert not policy.should_abort_request("https://auth.openai.com/login", "document")
    assert policy.should_abort_request("https://example.com/", "document")
    assert "--process-per-site" in policy.chromium_args()
    assert "--renderer-process-limit=2" in policy.chromium_args()


@pytest.mark.asyncio
async def test_storage_state_path_is_private(tmp_path):
    config = AppConfig.from_workspace(tmp_path, browser_mode="provider")
    browser = BrowserController(config)
    browser._context = FakeStorageContext()  # type: ignore[assignment]

    path = await browser.storage_state_path()

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
