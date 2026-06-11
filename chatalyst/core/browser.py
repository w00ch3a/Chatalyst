from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from loguru import logger
from playwright.async_api import BrowserContext, Page, Playwright, Route, async_playwright

from chatalyst.core.config import AppConfig, BrowserMode, BrowserProfile
from chatalyst.core.events import EventSink, EventType, NullEventSink, WorkspaceEvent
from chatalyst.core.models import BrowserSessionStatus, BrowserState, LoginState


class BrowserUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class BrowserLaunchPlanner:
    browser_mode: BrowserMode

    def headless_for_start(self, visible: bool | None, *, force_headless: bool = False) -> bool:
        if visible is True:
            return False
        if visible is False:
            return True
        return force_headless or self.browser_mode in {BrowserMode.HEADLESS, BrowserMode.SLEEP}

    @property
    def should_sleep_after_work(self) -> bool:
        return self.browser_mode in {
            BrowserMode.BACKGROUND,
            BrowserMode.PROVIDER,
            BrowserMode.SLEEP,
        }

    @property
    def should_background_window(self) -> bool:
        return self.browser_mode in {BrowserMode.BACKGROUND, BrowserMode.PROVIDER}

    @property
    def should_restart_headless_after_login(self) -> bool:
        return self.browser_mode is BrowserMode.AUTO


@dataclass(frozen=True)
class BrowserOptimizationPolicy:
    """Lean Chromium policy for a text-only ChatGPT workspace."""

    blocked_resource_types: tuple[str, ...]
    blocked_url_fragments: tuple[str, ...]
    allowed_document_hosts: tuple[str, ...]
    extra_args: tuple[str, ...]

    @classmethod
    def for_chatgpt_text_workspace(cls) -> BrowserOptimizationPolicy:
        return cls(
            blocked_resource_types=("image", "media", "font"),
            blocked_url_fragments=(
                "google-analytics.com",
                "googletagmanager.com",
                "doubleclick.net",
                "analytics.",
                "/analytics/",
                "/telemetry/",
                "/collect",
                "/beacon",
                "sentry.io",
            ),
            allowed_document_hosts=(),
            extra_args=(
                "--disable-background-networking",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-breakpad",
                "--disable-client-side-phishing-detection",
                "--disable-component-update",
                "--disable-default-apps",
                "--disable-domain-reliability",
                "--disable-extensions",
                "--disable-features=AutofillServerCommunication,CalculateNativeWinOcclusion,"
                "InterestFeedContentSuggestions,MediaRouter,OptimizationHints,Translate",
                "--disable-hang-monitor",
                "--disable-notifications",
                "--disable-popup-blocking",
                "--disable-prompt-on-repost",
                "--disable-renderer-backgrounding",
                "--disable-search-engine-choice-screen",
                "--disable-sync",
                "--disable-translate",
                "--metrics-recording-only",
                "--mute-audio",
                "--no-default-browser-check",
                "--no-first-run",
            ),
        )

    @classmethod
    def ultralight_for_chatgpt_text_workspace(cls) -> BrowserOptimizationPolicy:
        return cls(
            blocked_resource_types=("image", "media", "font", "stylesheet"),
            blocked_url_fragments=(
                "google-analytics.com",
                "googletagmanager.com",
                "doubleclick.net",
                "analytics.",
                "/analytics/",
                "/telemetry/",
                "/collect",
                "/beacon",
                "sentry.io",
                "segment.io",
                "statsig",
                "intercom",
                "fullstory",
                "hotjar",
            ),
            allowed_document_hosts=(
                "chatgpt.com",
                "chat.openai.com",
                "auth.openai.com",
                "auth0.openai.com",
            ),
            extra_args=(
                "--disable-background-networking",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-breakpad",
                "--disable-client-side-phishing-detection",
                "--disable-component-update",
                "--disable-default-apps",
                "--disable-features=AudioServiceOutOfProcess,AutofillServerCommunication,"
                "CalculateNativeWinOcclusion,InterestFeedContentSuggestions,MediaRouter,"
                "OptimizationHints,PaintHolding,Prerender2,SpeculationRulesPrefetchProxy,"
                "Translate",
                "--disable-ipc-flooding-protection",
                "--disable-dev-shm-usage",
                "--disable-domain-reliability",
                "--disable-extensions",
                "--disable-gpu",
                "--disable-hang-monitor",
                "--disable-notifications",
                "--disable-popup-blocking",
                "--disable-prompt-on-repost",
                "--disable-renderer-backgrounding",
                "--disable-search-engine-choice-screen",
                "--disable-smooth-scrolling",
                "--disable-sync",
                "--disable-translate",
                "--hide-scrollbars",
                "--metrics-recording-only",
                "--mute-audio",
                "--no-default-browser-check",
                "--no-first-run",
                "--no-pings",
                "--process-per-site",
            ),
        )

    @classmethod
    def from_config(cls, config: AppConfig) -> BrowserOptimizationPolicy:
        if config.browser_profile is BrowserProfile.ULTRALIGHT:
            return cls.ultralight_for_chatgpt_text_workspace()
        return cls.for_chatgpt_text_workspace()

    def chromium_args(self) -> list[str]:
        return [
            "--disable-blink-features=AutomationControlled",
            *self.extra_args,
        ]

    def should_abort_request(self, url: str, resource_type: str) -> bool:
        if resource_type == "document" and not self._document_host_allowed(url):
            return True
        if resource_type in self.blocked_resource_types:
            return True
        lowered = url.lower()
        return any(fragment in lowered for fragment in self.blocked_url_fragments)

    def _document_host_allowed(self, url: str) -> bool:
        if not self.allowed_document_hosts:
            return True
        hostname = urlparse(url).hostname
        if hostname is None:
            return True
        return any(
            hostname == allowed_host or hostname.endswith(f".{allowed_host}")
            for allowed_host in self.allowed_document_hosts
        )


@dataclass(frozen=True)
class BrowserDisplayPruningPolicy:
    """DOM slimming for already-cached ChatGPT display content."""

    enabled: bool
    retain_recent_turns: int
    retain_sidebar_items: int

    @classmethod
    def for_chatgpt_text_workspace(cls) -> BrowserDisplayPruningPolicy:
        return cls(enabled=True, retain_recent_turns=12, retain_sidebar_items=80)

    @classmethod
    def from_config(cls, config: AppConfig) -> BrowserDisplayPruningPolicy:
        return cls(
            enabled=True,
            retain_recent_turns=config.browser_retain_recent_turns,
            retain_sidebar_items=config.browser_retain_sidebar_items,
        )

    def javascript(self) -> str:
        return """
        ({ retainRecentTurns, retainSidebarItems }) => {
            const styleId = 'chatgpt-tui-prune-style';
            if (!document.getElementById(styleId)) {
                const style = document.createElement('style');
                style.id = styleId;
                style.textContent = `
                    [data-chatgpt-tui-pruned="true"] {
                        content-visibility: auto !important;
                        contain: layout paint style !important;
                        min-height: 2.25rem !important;
                        max-height: 2.25rem !important;
                        overflow: hidden !important;
                        opacity: .35 !important;
                    }
                    [data-chatgpt-tui-pruned="true"] > * {
                        visibility: hidden !important;
                    }
                    [data-chatgpt-tui-sidebar-pruned="true"] {
                        display: none !important;
                    }
                `;
                document.head.appendChild(style);
            }

            const messageSelector = [
                '[data-message-author-role]',
                'article[data-testid*="conversation-turn"]',
                'main article'
            ].join(', ');
            const turns = Array.from(document.querySelectorAll(messageSelector))
                .filter((node) => node.textContent?.trim());
            const pruneUntil = Math.max(0, turns.length - retainRecentTurns);
            turns.slice(0, pruneUntil).forEach((node, index) => {
                if (node.dataset.chatgptTuiPruned === 'true') return;
                const textLength = (node.innerText || node.textContent || '').length;
                node.dataset.chatgptTuiPruned = 'true';
                node.dataset.chatgptTuiOriginalTextLength = String(textLength);
                node.dataset.chatgptTuiPrunedTurn = String(index + 1);
            });

            const links = Array.from(document.querySelectorAll('a[href*="/c/"]'))
                .filter((node) => node.textContent?.trim());
            links.slice(retainSidebarItems).forEach((node) => {
                node.dataset.chatgptTuiSidebarPruned = 'true';
            });

            return {
                prunedTurns: pruneUntil,
                visibleTurns: Math.min(turns.length, retainRecentTurns),
                prunedSidebarItems: Math.max(0, links.length - retainSidebarItems)
            };
        }
        """


class BrowserController:
    """Owns the persistent Playwright browser profile."""

    def __init__(
        self,
        config: AppConfig,
        events: EventSink | None = None,
        optimization_policy: BrowserOptimizationPolicy | None = None,
    ) -> None:
        self.config = config
        self.events = events or NullEventSink()
        self.optimization_policy = (
            optimization_policy or BrowserOptimizationPolicy.from_config(config)
        )
        self.launch_planner = BrowserLaunchPlanner(config.browser_mode)
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._headless: bool | None = None
        self.status = BrowserSessionStatus(offline=config.offline)

    @property
    def page(self) -> Page:
        if self._page is None:
            raise BrowserUnavailableError("Browser page is not available.")
        return self._page

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise BrowserUnavailableError("Browser context is not available.")
        return self._context

    async def start(self, *, visible: bool | None = None) -> Page:
        if self.config.offline:
            self.status = self.status.model_copy(
                update={"browser_state": BrowserState.STOPPED, "offline": True}
            )
            raise BrowserUnavailableError("Offline mode does not start a browser.")
        if self._page is not None:
            if not self._page.is_closed():
                return self._page
            self._page = None
        if self._context is not None:
            open_pages = [page for page in self._context.pages if not page.is_closed()]
            self._page = open_pages[0] if open_pages else await self._context.new_page()
            self.status = self.status.model_copy(update={"browser_state": BrowserState.READY})
            return self._page
        self.config.ensure_runtime_dirs()
        self.status = self.status.model_copy(update={"browser_state": BrowserState.STARTING})
        self.events.emit(WorkspaceEvent(EventType.BROWSER_STARTED, "Starting Chromium"))
        self._playwright = await async_playwright().start()
        headless = self.launch_planner.headless_for_start(
            visible,
            force_headless=self.config.headless,
        )
        args = self.optimization_policy.chromium_args()
        if self.launch_planner.should_background_window and not headless:
            args.extend(("--window-position=-32000,-32000", "--window-size=1280,900"))
        logger.info("Launching persistent Chromium profile at {}", self.config.profile_dir)
        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.config.profile_dir),
                headless=headless,
                viewport={
                    "width": self.config.browser_viewport_width,
                    "height": self.config.browser_viewport_height,
                },
                accept_downloads=False,
                color_scheme="dark",
                reduced_motion="reduce",
                permissions=[],
                service_workers="block",
                args=args,
                timeout=self.config.launch_timeout_ms,
            )
        except Exception:
            await self._playwright.stop()
            self._playwright = None
            self.status = self.status.model_copy(update={"browser_state": BrowserState.ERROR})
            raise
        self._headless = headless
        self._context.set_default_timeout(self.config.selector_timeout_ms)
        self._context.set_default_navigation_timeout(self.config.launch_timeout_ms)
        await self._context.route("**/*", self._route_optimized_request)
        self._page = (
            self._context.pages[0] if self._context.pages else await self._context.new_page()
        )
        await self._close_extra_pages(self._page)
        if self.launch_planner.should_background_window and not headless:
            await self._minimize_window(self._page)
        self.status = self.status.model_copy(update={"browser_state": BrowserState.READY})
        return self._page

    async def open_chatgpt(self) -> Page:
        page = await self.start()
        if not page.url.startswith(self.config.chatgpt_url):
            await page.goto(self.config.chatgpt_url, wait_until="domcontentloaded")
        return page

    async def reveal_browser(self) -> None:
        if self._page is None:
            await self.start(visible=True)
            await self.open_chatgpt()
            return
        await self.page.bring_to_front()

    async def restart_visible(self) -> None:
        await self.stop()
        await self.start(visible=True)
        await self.open_chatgpt()

    async def restart_headless_after_login(self) -> None:
        if not self.launch_planner.should_restart_headless_after_login:
            return
        if self._page is None:
            return
        await self.stop()
        await self.start(visible=False)
        await self.open_chatgpt()

    async def park_after_work(self) -> None:
        if self.launch_planner.should_sleep_after_work:
            await self.stop()
            return
        if self.launch_planner.should_restart_headless_after_login and self._headless is False:
            await self.restart_headless_after_login()

    async def stop(self) -> None:
        context = self._context
        playwright = self._playwright
        self._context = None
        self._playwright = None
        self._page = None
        self._headless = None
        if context is not None:
            try:
                await context.close()
            except Exception as exc:
                logger.warning("Ignoring Chromium context close failure: {}", exc)
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception as exc:
                logger.warning("Ignoring Playwright stop failure: {}", exc)
        self.status = self.status.model_copy(
            update={"browser_state": BrowserState.STOPPED, "login_state": LoginState.UNKNOWN}
        )
        self.events.emit(WorkspaceEvent(EventType.BROWSER_STOPPED, "Chromium stopped"))

    async def storage_state_path(self) -> Path:
        self.config.ensure_runtime_dirs()
        path = self.config.profile_dir / "storage-state.json"
        await self.context.storage_state(path=str(path))
        path.chmod(0o600)
        return path

    async def _route_optimized_request(self, route: Route) -> None:
        request = route.request
        if self.optimization_policy.should_abort_request(request.url, request.resource_type):
            await route.abort()
            return
        await route.continue_()

    async def _close_extra_pages(self, main_page: Page) -> None:
        for page in list(self.context.pages):
            if page != main_page and not page.is_closed():
                await page.close()

    async def _minimize_window(self, page: Page) -> None:
        try:
            session = await self.context.new_cdp_session(page)
            window = await session.send("Browser.getWindowForTarget")
            await session.send(
                "Browser.setWindowBounds",
                {
                    "windowId": window["windowId"],
                    "bounds": {"windowState": "minimized"},
                },
            )
            await session.detach()
        except Exception as exc:
            logger.debug("Unable to minimize background Chromium window: {}", exc)
