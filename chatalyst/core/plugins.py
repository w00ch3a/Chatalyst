from __future__ import annotations

import importlib.util
import inspect
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from loguru import logger
from pydantic import BaseModel, Field, ValidationError, field_validator

from chatalyst.core.cache import ChatCache
from chatalyst.core.config import AppConfig
from chatalyst.core.models import Conversation, Message, SearchResult

JsonObject = dict[str, Any]
PluginToolHandler = Callable[["PluginContext", JsonObject], JsonObject | Awaitable[JsonObject]]

_PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class PluginPermission(StrEnum):
    VAULT_READ = "vault.read"
    VAULT_WRITE = "vault.write"
    SEARCH_EXTEND = "search.extend"
    EXPORT_OBSERVE = "export.observe"
    MCP_TOOLS = "mcp.tools"
    NETWORK = "network"
    TERMINAL = "terminal"


class PluginManifest(BaseModel):
    name: str
    version: str = "0.1.0"
    description: str = ""
    module: str
    factory: str = "create_plugin"
    enabled: bool = True
    permissions: tuple[PluginPermission, ...] = Field(default_factory=tuple)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        stripped = value.strip()
        if not _PLUGIN_NAME_RE.fullmatch(stripped) or stripped.startswith("."):
            raise ValueError("plugin name must be a safe local slug")
        return stripped

    @field_validator("module")
    @classmethod
    def validate_module(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("plugin module must not be blank")
        return stripped

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9_]+", "_", self.name.casefold()).strip("_")


@dataclass(frozen=True)
class PluginContext:
    config: AppConfig
    cache: ChatCache

    @classmethod
    def for_paths(cls, *, cache: ChatCache, workspace: Path) -> PluginContext:
        return cls(config=AppConfig.from_workspace(workspace), cache=cache)


@dataclass(frozen=True)
class LoadedPlugin:
    manifest: PluginManifest
    instance: WorkspacePlugin
    root: Path


@dataclass(frozen=True)
class MCPToolRegistration:
    name: str
    description: str
    input_schema: JsonObject
    read_only: bool
    plugin: str
    handler: PluginToolHandler

    @property
    def external_name(self) -> str:
        return f"chatalyst_plugin_{self.plugin}_{self.name}"

    def spec(self) -> JsonObject:
        return {
            "name": self.external_name,
            "description": self.description,
            "annotations": {"readOnlyHint": self.read_only, "destructiveHint": False},
            "inputSchema": self.input_schema,
        }

    async def call(self, context: PluginContext, arguments: JsonObject) -> JsonObject:
        result = self.handler(context, arguments)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, dict):
            raise TypeError(f"Plugin MCP tool {self.external_name} returned non-object result")
        return result


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
    """Local plugin hook registry with manifest validation and MCP tool support."""

    def __init__(self, *, audit_path: Path | None = None) -> None:
        self.audit_path = audit_path
        self._loaded: list[LoadedPlugin] = []

    @property
    def plugins(self) -> tuple[WorkspacePlugin, ...]:
        return tuple(loaded.instance for loaded in self._loaded)

    @property
    def manifests(self) -> tuple[PluginManifest, ...]:
        return tuple(loaded.manifest for loaded in self._loaded)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(loaded.manifest.name for loaded in self._loaded)

    def register(
        self,
        plugin: WorkspacePlugin,
        manifest: PluginManifest | None = None,
        *,
        root: Path | None = None,
    ) -> None:
        resolved_manifest = manifest or PluginManifest(
            name=getattr(plugin, "name", plugin.__class__.__name__),
            module="<registered>",
            description=getattr(plugin, "description", ""),
        )
        self._loaded.append(
            LoadedPlugin(
                manifest=resolved_manifest,
                instance=plugin,
                root=root or Path.cwd(),
            )
        )

    def load_from_directory(self, plugins_dir: Path) -> None:
        if not plugins_dir.exists():
            return
        for manifest_path in sorted(plugins_dir.glob("*/plugin.json")):
            self._load_manifest(manifest_path)

    def _load_manifest(self, manifest_path: Path) -> None:
        manifest: PluginManifest | None = None
        try:
            raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(raw_manifest, dict):
                self._audit("plugin_rejected", manifest_path, reason="manifest_not_object")
                return
            raw_manifest.setdefault("name", manifest_path.parent.name)
            raw_manifest.setdefault("factory", "create_plugin")
            manifest = PluginManifest.model_validate(raw_manifest)
            if not manifest.enabled:
                self._audit("plugin_disabled", manifest_path, manifest=manifest)
                return
            if any(loaded.manifest.slug == manifest.slug for loaded in self._loaded):
                self._audit(
                    "plugin_rejected",
                    manifest_path,
                    manifest=manifest,
                    reason="duplicate_plugin_slug",
                )
                return
            module_path = (manifest_path.parent / manifest.module).resolve()
            plugin_root = manifest_path.parent.resolve()
            if not module_path.is_file() or plugin_root not in module_path.parents:
                logger.warning(
                    "Plugin manifest {} points outside its plugin folder.",
                    manifest_path,
                )
                self._audit("plugin_rejected", manifest_path, manifest=manifest, reason="bad_path")
                return
            spec = importlib.util.spec_from_file_location(
                f"chatalyst_user_plugin_{manifest.slug}", module_path
            )
            if spec is None or spec.loader is None:
                logger.warning("Unable to load plugin module from {}", module_path)
                self._audit("plugin_rejected", manifest_path, manifest=manifest, reason="no_loader")
                return
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            factory = getattr(module, manifest.factory, None)
            if factory is None:
                logger.warning("Plugin {} has no factory {}.", module_path, manifest.factory)
                self._audit(
                    "plugin_rejected",
                    manifest_path,
                    manifest=manifest,
                    reason="missing_factory",
                )
                return
            self.register(factory(), manifest, root=plugin_root)
            self._audit("plugin_loaded", manifest_path, manifest=manifest)
        except ValidationError as exc:
            logger.warning("Invalid plugin manifest {}: {}", manifest_path, exc)
            self._audit("plugin_rejected", manifest_path, manifest=manifest, reason="invalid")
        except Exception:
            logger.exception("Failed to load plugin manifest {}", manifest_path)
            self._audit("plugin_error", manifest_path, manifest=manifest, reason="exception")

    def mcp_tools(self, context: PluginContext) -> tuple[MCPToolRegistration, ...]:
        tools: list[MCPToolRegistration] = []
        external_names: set[str] = set()
        for loaded in self._loaded:
            hook = getattr(loaded.instance, "mcp_tools", None)
            if hook is None:
                continue
            if PluginPermission.MCP_TOOLS not in loaded.manifest.permissions:
                self._audit(
                    "plugin_tool_denied",
                    loaded.root / "plugin.json",
                    manifest=loaded.manifest,
                    reason="missing_mcp_tools_permission",
                )
                continue
            try:
                raw_tools = hook(context)
                if inspect.isawaitable(raw_tools):
                    raise TypeError("mcp_tools must return tool definitions synchronously")
                if raw_tools is None:
                    continue
                if not isinstance(raw_tools, list | tuple):
                    raise TypeError("mcp_tools must return a list or tuple")
                for raw_tool in raw_tools:
                    registration = self._coerce_mcp_tool(loaded, raw_tool)
                    if registration is not None:
                        if registration.external_name in external_names:
                            self._audit(
                                "plugin_tool_rejected",
                                loaded.root / "plugin.json",
                                manifest=loaded.manifest,
                                reason="duplicate_external_name",
                            )
                            continue
                        external_names.add(registration.external_name)
                        tools.append(registration)
            except Exception:
                logger.exception(
                    "Plugin {} failed while registering MCP tools",
                    loaded.manifest.name,
                )
                self._audit(
                    "plugin_tool_error",
                    loaded.root / "plugin.json",
                    manifest=loaded.manifest,
                    reason="exception",
                )
        return tuple(tools)

    def _coerce_mcp_tool(
        self,
        loaded: LoadedPlugin,
        raw_tool: object,
    ) -> MCPToolRegistration | None:
        if not isinstance(raw_tool, dict):
            self._audit(
                "plugin_tool_rejected",
                loaded.root / "plugin.json",
                manifest=loaded.manifest,
                reason="tool_not_object",
            )
            return None
        name = str(raw_tool.get("name") or "").strip()
        if not _TOOL_NAME_RE.fullmatch(name):
            self._audit(
                "plugin_tool_rejected",
                loaded.root / "plugin.json",
                manifest=loaded.manifest,
                reason="bad_tool_name",
            )
            return None
        description = str(raw_tool.get("description") or "").strip()
        if not description:
            self._audit(
                "plugin_tool_rejected",
                loaded.root / "plugin.json",
                manifest=loaded.manifest,
                reason="missing_description",
            )
            return None
        input_schema = raw_tool.get("input_schema") or raw_tool.get("inputSchema")
        if not isinstance(input_schema, dict):
            self._audit(
                "plugin_tool_rejected",
                loaded.root / "plugin.json",
                manifest=loaded.manifest,
                reason="bad_input_schema",
            )
            return None
        handler = raw_tool.get("handler")
        if not callable(handler):
            self._audit(
                "plugin_tool_rejected",
                loaded.root / "plugin.json",
                manifest=loaded.manifest,
                reason="missing_handler",
            )
            return None
        if "read_only" not in raw_tool:
            self._audit(
                "plugin_tool_rejected",
                loaded.root / "plugin.json",
                manifest=loaded.manifest,
                reason="missing_read_only_flag",
            )
            return None
        read_only = raw_tool["read_only"]
        if not isinstance(read_only, bool):
            self._audit(
                "plugin_tool_rejected",
                loaded.root / "plugin.json",
                manifest=loaded.manifest,
                reason="bad_read_only_flag",
            )
            return None
        if not read_only and PluginPermission.VAULT_WRITE not in loaded.manifest.permissions:
            self._audit(
                "plugin_tool_denied",
                loaded.root / "plugin.json",
                manifest=loaded.manifest,
                reason="missing_vault_write_permission",
            )
            return None
        return MCPToolRegistration(
            name=name,
            description=description,
            input_schema=input_schema,
            read_only=read_only,
            plugin=loaded.manifest.slug,
            handler=handler,
        )

    def startup(self, context: PluginContext) -> None:
        for plugin in self.plugins:
            hook = getattr(plugin, "on_startup", None)
            if hook:
                hook(context)

    def conversation_opened(self, context: PluginContext, conversation_id: str) -> None:
        for plugin in self.plugins:
            hook = getattr(plugin, "on_conversation_opened", None)
            if hook:
                hook(context, conversation_id)

    def message_cached(self, context: PluginContext, message: Message) -> None:
        for plugin in self.plugins:
            hook = getattr(plugin, "on_message_cached", None)
            if hook:
                hook(context, message)

    def search_results(
        self, context: PluginContext, query: str, results: list[SearchResult]
    ) -> list[SearchResult]:
        current = results
        for plugin in self.plugins:
            hook = getattr(plugin, "on_search_results", None)
            if hook:
                current = hook(context, query, current)
        return current

    def before_export(self, context: PluginContext, conversation: Conversation) -> None:
        for plugin in self.plugins:
            hook = getattr(plugin, "on_before_export", None)
            if hook:
                hook(context, conversation)

    def _audit(
        self,
        event: str,
        manifest_path: Path,
        *,
        manifest: PluginManifest | None = None,
        reason: str | None = None,
    ) -> None:
        if self.audit_path is None:
            return
        payload: JsonObject = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            "plugin": manifest.name if manifest else manifest_path.parent.name,
            "version": manifest.version if manifest else None,
            "permissions": [permission.value for permission in manifest.permissions]
            if manifest
            else [],
            "manifest_path": str(manifest_path),
        }
        if reason:
            payload["reason"] = reason
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self.audit_path.parent.chmod(0o700)
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
        self.audit_path.chmod(0o600)
