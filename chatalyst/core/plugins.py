from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from chatalyst.core.cache import ChatCache
from chatalyst.core.config import AppConfig
from chatalyst.core.models import Conversation, Message, SearchResult


@dataclass(frozen=True)
class PluginContext:
    config: AppConfig
    cache: ChatCache

    @classmethod
    def for_paths(cls, *, cache: ChatCache, workspace: Path) -> PluginContext:
        return cls(config=AppConfig.from_workspace(workspace), cache=cache)


class WorkspacePlugin(Protocol):
    name: str
    description: str

    def on_startup(self, context: PluginContext) -> None:
        ...

    def on_conversation_opened(self, context: PluginContext, conversation_id: str) -> None:
        ...

    def on_message_cached(self, context: PluginContext, message: Message) -> None:
        ...

    def on_search_results(
        self, context: PluginContext, query: str, results: list[SearchResult]
    ) -> list[SearchResult]:
        ...

    def on_before_export(self, context: PluginContext, conversation: Conversation) -> None:
        ...


class PluginRegistry:
    """Small hook registry reserved for future local integrations."""

    def __init__(self) -> None:
        self._plugins: list[WorkspacePlugin] = []

    @property
    def plugins(self) -> tuple[WorkspacePlugin, ...]:
        return tuple(self._plugins)

    def register(self, plugin: WorkspacePlugin) -> None:
        self._plugins.append(plugin)

    def startup(self, context: PluginContext) -> None:
        for plugin in self._plugins:
            hook = getattr(plugin, "on_startup", None)
            if hook:
                hook(context)

    def conversation_opened(self, context: PluginContext, conversation_id: str) -> None:
        for plugin in self._plugins:
            hook = getattr(plugin, "on_conversation_opened", None)
            if hook:
                hook(context, conversation_id)

    def message_cached(self, context: PluginContext, message: Message) -> None:
        for plugin in self._plugins:
            hook = getattr(plugin, "on_message_cached", None)
            if hook:
                hook(context, message)

    def search_results(
        self, context: PluginContext, query: str, results: list[SearchResult]
    ) -> list[SearchResult]:
        current = results
        for plugin in self._plugins:
            hook = getattr(plugin, "on_search_results", None)
            if hook:
                current = hook(context, query, current)
        return current

    def before_export(self, context: PluginContext, conversation: Conversation) -> None:
        for plugin in self._plugins:
            hook = getattr(plugin, "on_before_export", None)
            if hook:
                hook(context, conversation)
