from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from loguru import logger

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

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(getattr(plugin, "name", plugin.__class__.__name__) for plugin in self._plugins)

    def register(self, plugin: WorkspacePlugin) -> None:
        self._plugins.append(plugin)

    def load_from_directory(self, plugins_dir: Path) -> None:
        if not plugins_dir.exists():
            return
        for manifest_path in sorted(plugins_dir.glob("*/plugin.json")):
            self._load_manifest(manifest_path)

    def _load_manifest(self, manifest_path: Path) -> None:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            module_name = str(manifest.get("module") or "").strip()
            factory_name = str(manifest.get("factory") or "create_plugin").strip()
            if not module_name:
                logger.warning("Plugin manifest {} has no module.", manifest_path)
                return
            module_path = (manifest_path.parent / module_name).resolve()
            plugin_root = manifest_path.parent.resolve()
            if not module_path.is_file() or plugin_root not in module_path.parents:
                logger.warning(
                    "Plugin manifest {} points outside its plugin folder.",
                    manifest_path,
                )
                return
            spec = importlib.util.spec_from_file_location(
                f"chatalyst_user_plugin_{manifest_path.parent.name}", module_path
            )
            if spec is None or spec.loader is None:
                logger.warning("Unable to load plugin module from {}", module_path)
                return
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            factory = getattr(module, factory_name, None)
            if factory is None:
                logger.warning("Plugin {} has no factory {}.", module_path, factory_name)
                return
            self.register(factory())
        except Exception:
            logger.exception("Failed to load plugin manifest {}", manifest_path)

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
