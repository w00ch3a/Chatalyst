from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from chatalyst.core.browser import (
    BrowserController,
    BrowserDisplayPruningPolicy,
    BrowserUnavailableError,
)
from chatalyst.core.cache import ChatCache
from chatalyst.core.config import AppConfig
from chatalyst.core.events import EventSink, EventType, NullEventSink, WorkspaceEvent
from chatalyst.core.models import (
    BrowserSessionStatus,
    BrowserState,
    CodeBlock,
    Conversation,
    LoginState,
    Message,
    MessageRole,
    SyncStatus,
    utc_now,
)
from chatalyst.core.selectors import SelectorCatalog, SelectorDiagnostic, SelectorGroup


@dataclass(frozen=True)
class ExtractionResult:
    conversation: Conversation
    messages: list[Message]


class SelectorResolutionError(RuntimeError):
    def __init__(self, diagnostic: SelectorDiagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


class PromptSubmittedNoAssistantResponseError(RuntimeError):
    def __init__(self, conversation_id: str, prompt: str) -> None:
        super().__init__(
            "Prompt submission landed, but ChatGPT did not produce a new assistant "
            "response before the wait timeout."
        )
        self.conversation_id = conversation_id
        self.prompt = prompt


class ChatGPTService:
    """Extraction layer over ChatGPT's visible browser UI."""

    def __init__(
        self,
        config: AppConfig,
        browser: BrowserController,
        cache: ChatCache,
        selectors: SelectorCatalog | None = None,
        events: EventSink | None = None,
        pruning_policy: BrowserDisplayPruningPolicy | None = None,
    ) -> None:
        self.config = config
        self.browser = browser
        self.cache = cache
        self.selectors = selectors or SelectorCatalog()
        self.events = events or NullEventSink()
        self.pruning_policy = pruning_policy or BrowserDisplayPruningPolicy.from_config(config)

    async def status(self) -> BrowserSessionStatus:
        if self.config.offline:
            return BrowserSessionStatus(offline=True, browser_state=BrowserState.STOPPED)
        try:
            page = await self.browser.open_chatgpt()
            login = await self.login_state(page)
        except BrowserUnavailableError as exc:
            return BrowserSessionStatus(
                browser_state=BrowserState.ERROR,
                login_state=LoginState.UNKNOWN,
                diagnostic=str(exc),
            )
        return BrowserSessionStatus(browser_state=BrowserState.READY, login_state=login)

    async def login_state(self, page: Page | None = None) -> LoginState:
        page = page or await self.browser.open_chatgpt()
        if await self._any_visible(page, self.selectors.login_markers):
            return LoginState.LOGGED_OUT
        if await self._any_visible(page, self.selectors.composer):
            return LoginState.LOGGED_IN
        return LoginState.UNKNOWN

    async def discover_chats(self) -> list[Conversation]:
        page = await self.browser.open_chatgpt()
        conversations = await page.evaluate(
            """
            () => Array.from(document.querySelectorAll('a[href*="/c/"]')).map((node) => {
                const href = node.href || node.getAttribute('href') || '';
                const title = (node.innerText || node.textContent || '').trim();
                const project = node.closest('[data-testid*="project"], [role="treeitem"]')
                    ?.textContent?.trim() || null;
                return { href, title, project };
            }).filter((item) => item.href && item.title)
            """
        )
        extracted: list[Conversation] = []
        seen: set[str] = set()
        for item in conversations:
            url = str(item["href"])
            chat_id = self._conversation_id_from_url(url)
            if chat_id in seen:
                continue
            seen.add(chat_id)
            title = str(item["title"]).splitlines()[0].strip() or "Untitled"
            conversation = Conversation(
                id=chat_id,
                title=title,
                url=url,
                chat_identifier=chat_id,
                project_name=item.get("project"),
                sync_status=SyncStatus.STALE,
            )
            self.cache.upsert_conversation(conversation)
            extracted.append(conversation)
        self.cache.set_sync_state("last_discovery_at", utc_now().isoformat())
        await self.apply_display_pruning(page)
        self.events.emit(
            WorkspaceEvent(
                EventType.SYNC_FINISHED,
                f"Discovered {len(extracted)} conversations",
                {"count": len(extracted)},
            )
        )
        return extracted

    async def open_conversation(self, conversation_id: str) -> ExtractionResult:
        cached = self.cache.get_conversation(conversation_id)
        if cached is None:
            raise ValueError(f"Unknown conversation: {conversation_id}")
        if cached.url:
            page = await self.browser.start()
            await page.goto(cached.url, wait_until="domcontentloaded")
        else:
            page = await self.browser.open_chatgpt()
        await self._wait_for_any(page, self.selectors.message_blocks)
        extracted = await self.extract_current_conversation(cached)
        self.cache.mark_opened(conversation_id)
        for message in extracted.messages:
            self.cache.upsert_message(message)
        await self.apply_display_pruning(page)
        self.events.emit(
            WorkspaceEvent(EventType.CONVERSATION_OPENED, cached.title, {"id": conversation_id})
        )
        return extracted

    async def new_chat(self) -> Conversation:
        page = await self.browser.open_chatgpt()
        clicked = await self._click_first(page, self.selectors.new_chat_button)
        if not clicked:
            await page.goto(self.config.chatgpt_url, wait_until="domcontentloaded")
        await self._wait_for_any(page, self.selectors.composer)
        conversation = Conversation(
            id=self._hash_id(page.url or f"new-{utc_now().isoformat()}"),
            title="New Chat",
            url=page.url,
            sync_status=SyncStatus.SYNCING,
        )
        self.cache.upsert_conversation(conversation)
        return conversation

    async def send_message(
        self,
        prompt: str,
        *,
        response_timeout_seconds: float | None = None,
    ) -> AsyncIterator[Message]:
        if not prompt.strip():
            return
        page = await self.browser.open_chatgpt()
        starting_url = page.url
        conversation_id = self._conversation_id_from_url(starting_url)
        await self._wait_until_chatgpt_idle(page)
        baseline_messages = await self.extract_messages(page, conversation_id)
        if self.cache.get_conversation(conversation_id) is None:
            self.cache.upsert_conversation(
                Conversation(
                    id=conversation_id,
                    title="New Chat",
                    url=starting_url,
                    chat_identifier=conversation_id,
                    sync_status=SyncStatus.SYNCING,
                )
            )
        user_ordinal = len(baseline_messages)
        user_message = Message(
            id=self._message_id(conversation_id, MessageRole.USER, prompt, user_ordinal),
            conversation_id=conversation_id,
            role=MessageRole.USER,
            markdown=prompt,
            ordinal=user_ordinal,
        )
        self.cache.upsert_message(user_message)
        baseline_assistant_messages = [
            message for message in baseline_messages if message.role is MessageRole.ASSISTANT
        ]
        baseline_last_assistant = (
            baseline_assistant_messages[-1] if baseline_assistant_messages else None
        )
        baseline_last_ordinal = (
            baseline_last_assistant.ordinal if baseline_last_assistant is not None else -1
        )
        baseline_last_text = (
            baseline_last_assistant.markdown if baseline_last_assistant is not None else ""
        )
        composer = await self._locator(page, self.selectors.composer)
        await self._fill_composer(page, composer, prompt)
        if not await self._click_first(page, self.selectors.send_button):
            await composer.press("Enter")
        idle = 0.0
        elapsed = 0.0
        last_text = ""
        ordinal = 0
        saw_new_assistant = False
        timeout_seconds = (
            response_timeout_seconds
            if response_timeout_seconds is not None
            else self.config.assistant_response_timeout_seconds
        )
        max_wait = max(5.0, timeout_seconds)
        while True:
            messages = await self.extract_messages(page, conversation_id)
            assistant_messages = [
                message for message in messages if message.role is MessageRole.ASSISTANT
            ]
            new_assistant_messages = [
                message
                for message in assistant_messages
                if message.ordinal > baseline_last_ordinal
                or (
                    message.ordinal == baseline_last_ordinal
                    and message.markdown != baseline_last_text
                )
            ]
            if new_assistant_messages:
                current = new_assistant_messages[-1]
                ordinal = current.ordinal
                if current.markdown != last_text:
                    last_text = current.markdown
                    saw_new_assistant = True
                    existing_assistant = [
                        message
                        for message in self.cache.list_messages(conversation_id)
                        if message.role is MessageRole.ASSISTANT
                        and message.ordinal == current.ordinal
                    ]
                    if existing_assistant:
                        current = current.model_copy(update={"id": existing_assistant[-1].id})
                    streaming_message = current.model_copy(update={"is_streaming": True})
                    self.cache.upsert_message(streaming_message)
                    self.events.emit(
                        WorkspaceEvent(
                            EventType.MESSAGE_STREAMED,
                            "Assistant response updated",
                            {"conversation_id": conversation_id, "message_id": current.id},
                        )
                    )
                    yield streaming_message
                    idle = 0.0
            streaming = await self._any_visible(page, self.selectors.stop_button)
            await asyncio.sleep(self.config.response_poll_interval_seconds)
            idle += self.config.response_poll_interval_seconds
            elapsed += self.config.response_poll_interval_seconds
            if saw_new_assistant and not streaming and idle >= self.config.max_stream_idle_seconds:
                break
            if elapsed >= max_wait:
                break
        if not saw_new_assistant:
            actual_conversation_id = self._conversation_id_from_url(page.url)
            if actual_conversation_id != conversation_id:
                conversation = Conversation(
                    id=actual_conversation_id,
                    title=await self._extract_title(page, "New Chat"),
                    url=page.url,
                    chat_identifier=actual_conversation_id,
                    sync_status=SyncStatus.CACHED,
                )
                self.cache.reconcile_conversation_id(conversation_id, conversation)
                conversation_id = actual_conversation_id
                user_message = user_message.model_copy(
                    update={
                        "id": self._message_id(
                            actual_conversation_id,
                            MessageRole.USER,
                            prompt,
                            user_ordinal,
                        ),
                        "conversation_id": actual_conversation_id,
                    }
                )
            messages = await self.extract_messages(page, conversation_id)
            submitted = any(
                message.role is MessageRole.USER and message.markdown.strip() == prompt.strip()
                for message in messages
            )
            if submitted:
                for message in messages:
                    self.cache.upsert_message(message)
                await self.apply_display_pruning(page)
                raise PromptSubmittedNoAssistantResponseError(conversation_id, prompt)
            raise RuntimeError("Prompt submission did not produce a new assistant response.")
        final = Message(
            id=self._message_id(conversation_id, MessageRole.ASSISTANT, last_text, ordinal),
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            markdown=last_text,
            ordinal=ordinal,
            is_streaming=False,
            code_blocks=self._extract_code_blocks(last_text),
        )
        if last_text:
            actual_conversation_id = self._conversation_id_from_url(page.url)
            if actual_conversation_id != conversation_id:
                conversation_id = actual_conversation_id
                conversation = Conversation(
                    id=actual_conversation_id,
                    title=await self._extract_title(page, "New Chat"),
                    url=page.url,
                    chat_identifier=actual_conversation_id,
                    sync_status=SyncStatus.CACHED,
                )
                self.cache.reconcile_conversation_id(user_message.conversation_id, conversation)
                user_message = user_message.model_copy(
                    update={"conversation_id": actual_conversation_id}
                )
                final = final.model_copy(
                    update={
                        "id": self._message_id(
                            actual_conversation_id,
                            MessageRole.ASSISTANT,
                            last_text,
                            ordinal,
                        ),
                        "conversation_id": actual_conversation_id,
                    }
                )
            existing_assistant = [
                message
                for message in self.cache.list_messages(conversation_id)
                if message.role is MessageRole.ASSISTANT and message.ordinal == ordinal
            ]
            if existing_assistant:
                final = final.model_copy(update={"id": existing_assistant[-1].id})
            self.cache.upsert_message(final)
            await self.apply_display_pruning(page)
            yield final

    async def extract_current_conversation(
        self, cached: Conversation | None = None
    ) -> ExtractionResult:
        page = await self.browser.start()
        title = await self._extract_title(page, cached.title if cached else "Untitled")
        conversation_id = self._conversation_id_from_url(page.url)
        conversation = (cached or Conversation(id=conversation_id, title=title)).model_copy(
            update={
                "id": conversation_id,
                "title": title,
                "url": page.url,
                "chat_identifier": conversation_id,
                "updated_at": utc_now(),
                "sync_status": SyncStatus.CACHED,
            }
        )
        self.cache.upsert_conversation(conversation)
        messages = await self.extract_messages(page, conversation.id)
        for message in messages:
            self.cache.upsert_message(message)
        await self.apply_display_pruning(page)
        return ExtractionResult(conversation=conversation, messages=messages)

    async def extract_messages(self, page: Page, conversation_id: str) -> list[Message]:
        raw_messages = await page.evaluate(
            """
            () => {
                const nodes = Array.from(document.querySelectorAll(
                    [
                        '[data-message-author-role]',
                        'article[data-testid*="conversation-turn"]',
                        'main article'
                    ].join(', ')
                )).filter((node) => node.dataset.chatgptTuiPruned !== 'true');
                return nodes.map((node, index) => {
                    let role = node.getAttribute('data-message-author-role');
                    if (!role) {
                        const lowerText = (node.innerText || '').trim().toLowerCase();
                        role = lowerText.startsWith('you') ? 'user' : null;
                    }
                    const text = (node.innerText || node.textContent || '').trim();
                    const codeBlocks = Array.from(node.querySelectorAll('pre code')).map(
                        (code, ordinal) => {
                            const languageClass = Array.from(code.classList || [])
                                .find((name) => name.startsWith('language-'));
                            return {
                                language: languageClass?.replace('language-', '') || null,
                                code: code.textContent || '',
                                ordinal
                            };
                        }
                    );
                    return { role, text, codeBlocks, index };
                }).filter((item) => item.text);
            }
            """
        )
        messages: list[Message] = []
        for item in raw_messages:
            role = self._normalize_role(item.get("role"), item.get("text", ""))
            text = str(item.get("text", "")).strip()
            ordinal = int(item.get("index", len(messages)))
            blocks = [CodeBlock(**block) for block in item.get("codeBlocks", [])]
            if not blocks:
                blocks = self._extract_code_blocks(text)
            messages.append(
                Message(
                    id=self._message_id(conversation_id, role, text, ordinal),
                    conversation_id=conversation_id,
                    role=role,
                    markdown=text,
                    ordinal=ordinal,
                    code_blocks=blocks,
                )
            )
        return messages

    async def apply_display_pruning(self, page: Page | None = None) -> dict[str, int]:
        if not self.pruning_policy.enabled:
            return {"prunedTurns": 0, "visibleTurns": 0, "prunedSidebarItems": 0}
        page = page or await self.browser.start()
        result = await page.evaluate(
            self.pruning_policy.javascript(),
            {
                "retainRecentTurns": self.pruning_policy.retain_recent_turns,
                "retainSidebarItems": self.pruning_policy.retain_sidebar_items,
            },
        )
        return {
            "prunedTurns": int(result.get("prunedTurns", 0)),
            "visibleTurns": int(result.get("visibleTurns", 0)),
            "prunedSidebarItems": int(result.get("prunedSidebarItems", 0)),
        }

    async def _locator(self, page: Page, group: SelectorGroup) -> Locator:
        for selector in group.candidates:
            locator = page.locator(selector).first
            try:
                await locator.wait_for(timeout=self.config.selector_timeout_ms)
                return locator
            except PlaywrightTimeoutError:
                continue
        diagnostic = SelectorDiagnostic(
            selector_group=group.name,
            attempted=group.candidates,
            url=page.url,
            message=f"Unable to resolve selector group: {group.name}",
        )
        self.events.emit(
            WorkspaceEvent(
                EventType.SELECTOR_FAILED,
                diagnostic.message,
                {"group": group.name, "url": page.url, "attempted": list(group.candidates)},
            )
        )
        raise SelectorResolutionError(diagnostic)

    async def _wait_for_any(self, page: Page, group: SelectorGroup) -> None:
        await self._locator(page, group)

    async def _fill_composer(self, page: Page, composer: Locator, prompt: str) -> None:
        await composer.click()
        tag_name = await composer.evaluate("(node) => node.tagName.toLowerCase()")
        if tag_name == "textarea":
            await composer.fill(prompt)
            return

        await composer.evaluate(
            """
            (node) => {
                node.textContent = '';
                node.dispatchEvent(new InputEvent('input', {
                    bubbles: true,
                    inputType: 'deleteContentBackward'
                }));
            }
            """
        )
        await composer.click()
        await page.keyboard.insert_text(prompt)
        await page.wait_for_timeout(250)

    async def _wait_until_chatgpt_idle(self, page: Page) -> None:
        elapsed = 0.0
        max_wait = max(30.0, self.config.launch_timeout_ms / 1000)
        while await self._any_visible(page, self.selectors.stop_button):
            await asyncio.sleep(self.config.response_poll_interval_seconds)
            elapsed += self.config.response_poll_interval_seconds
            if elapsed >= max_wait:
                raise RuntimeError(
                    "ChatGPT is still responding in this conversation. "
                    "Wait for the current response to finish, then retry."
                )

    async def _any_visible(self, page: Page, group: SelectorGroup) -> bool:
        for selector in group.candidates:
            try:
                if await page.locator(selector).first.is_visible(timeout=1_000):
                    return True
            except Exception:
                continue
        return False

    async def _click_first(self, page: Page, group: SelectorGroup) -> bool:
        for selector in group.candidates:
            locator = page.locator(selector).first
            try:
                if await locator.is_visible(timeout=1_000):
                    await locator.click()
                    return True
            except Exception:
                continue
        return False

    async def _extract_title(self, page: Page, fallback: str) -> str:
        value = await page.evaluate(
            """
            () => {
                const h1 = document.querySelector('h1');
                if (h1?.innerText?.trim()) return h1.innerText.trim();
                const title = document.title || '';
                return title.replace(/^ChatGPT\\s*-?\\s*/i, '').trim();
            }
            """
        )
        return str(value or fallback or "Untitled").splitlines()[0]

    def _conversation_id_from_url(self, url: str) -> str:
        match = re.search(r"/c/([^/?#]+)", url or "")
        if match:
            return match.group(1)
        return self._hash_id(url or "local-chat")

    def _message_id(self, conversation_id: str, role: MessageRole, text: str, ordinal: int) -> str:
        return self._hash_id(f"{conversation_id}:{role.value}:{ordinal}:{text[:256]}")

    def _hash_id(self, value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]

    def _normalize_role(self, raw: object, text: str) -> MessageRole:
        value = str(raw or "").lower()
        if value in {"user", "assistant", "system", "tool"}:
            return MessageRole(value)
        lowered = text.strip().lower()
        if lowered.startswith("you") or lowered.startswith("user"):
            return MessageRole.USER
        if lowered.startswith("chatgpt") or lowered.startswith("assistant"):
            return MessageRole.ASSISTANT
        return MessageRole.UNKNOWN

    def _extract_code_blocks(self, markdown: str) -> list[CodeBlock]:
        matches = re.finditer(r"```([a-zA-Z0-9_-]+)?\n(.*?)```", markdown, flags=re.DOTALL)
        return [
            CodeBlock(language=match.group(1), code=match.group(2).rstrip(), ordinal=index)
            for index, match in enumerate(matches)
        ]
