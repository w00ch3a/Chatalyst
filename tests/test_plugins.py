from __future__ import annotations

import json

from chatalyst.core.cache import ChatCache
from chatalyst.core.config import AppConfig
from chatalyst.core.plugins import PluginContext, PluginRegistry


def test_plugin_registry_loads_manifest_plugin(tmp_path):
    plugin_dir = tmp_path / "plugins" / "sample"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"module": "plugin.py", "factory": "create_plugin"}),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        """
class SamplePlugin:
    name = "sample"
    description = "Sample plugin"

def create_plugin():
    return SamplePlugin()
""",
        encoding="utf-8",
    )

    registry = PluginRegistry()
    registry.load_from_directory(tmp_path / "plugins")

    assert registry.names == ("sample",)


def test_plugin_registry_loads_dataclass_plugin(tmp_path):
    plugin_dir = tmp_path / "plugins" / "dataclass_plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "dataclass_plugin",
                "module": "plugin.py",
                "factory": "create_plugin",
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        """
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class DataclassPlugin:
    name: str = "dataclass_plugin"
    description: str = "Dataclass plugin"

def create_plugin():
    return DataclassPlugin()
""",
        encoding="utf-8",
    )

    registry = PluginRegistry()
    registry.load_from_directory(tmp_path / "plugins")

    assert registry.names == ("dataclass_plugin",)


def test_plugin_registry_rejects_manifest_outside_plugin_folder(tmp_path):
    plugin_dir = tmp_path / "plugins" / "bad"
    plugin_dir.mkdir(parents=True)
    outside = tmp_path / "outside.py"
    outside.write_text(
        "def create_plugin():\n    raise AssertionError('loaded')\n",
        encoding="utf-8",
    )
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"module": "../outside.py"}),
        encoding="utf-8",
    )

    registry = PluginRegistry()
    registry.load_from_directory(tmp_path / "plugins")

    assert registry.plugins == ()


def test_plugin_registry_skips_disabled_plugin(tmp_path):
    plugin_dir = tmp_path / "plugins" / "sleeping"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "sleeping",
                "module": "plugin.py",
                "factory": "create_plugin",
                "enabled": False,
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        "def create_plugin():\n    raise AssertionError('disabled plugin loaded')\n",
        encoding="utf-8",
    )

    registry = PluginRegistry()
    registry.load_from_directory(tmp_path / "plugins")

    assert registry.plugins == ()


def test_plugin_registry_rejects_unknown_permission(tmp_path):
    plugin_dir = tmp_path / "plugins" / "badperm"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "badperm",
                "module": "plugin.py",
                "permissions": ["vault.read", "browser.control"],
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        "def create_plugin():\n    raise AssertionError('bad permission plugin loaded')\n",
        encoding="utf-8",
    )

    registry = PluginRegistry()
    registry.load_from_directory(tmp_path / "plugins")

    assert registry.plugins == ()


def test_plugin_registry_writes_load_audit_log(tmp_path):
    plugin_dir = tmp_path / "plugins" / "sample"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "sample",
                "version": "1.2.3",
                "module": "plugin.py",
                "factory": "create_plugin",
                "permissions": ["vault.read"],
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        """
class SamplePlugin:
    name = "sample"
    description = "Sample plugin"

def create_plugin():
    return SamplePlugin()
""",
        encoding="utf-8",
    )
    audit_path = tmp_path / "logs" / "plugin-audit.jsonl"

    registry = PluginRegistry(audit_path=audit_path)
    registry.load_from_directory(tmp_path / "plugins")

    events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert registry.names == ("sample",)
    assert events[-1]["event"] == "plugin_loaded"
    assert events[-1]["plugin"] == "sample"
    assert events[-1]["version"] == "1.2.3"
    assert oct(audit_path.stat().st_mode & 0o777) == "0o600"


def test_plugin_mcp_tool_requires_permission(tmp_path):
    plugin_dir = tmp_path / "plugins" / "tooling"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "tooling",
                "module": "plugin.py",
                "factory": "create_plugin",
                "permissions": ["vault.read"],
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        """
class ToolingPlugin:
    name = "tooling"
    description = "Tooling plugin"

    def mcp_tools(self, context):
        return [{
            "name": "echo",
            "description": "Echo a value",
            "input_schema": {"type": "object", "properties": {"value": {"type": "string"}}},
            "handler": self.echo,
        }]

    def echo(self, context, arguments):
        return {"value": arguments.get("value")}

def create_plugin():
    return ToolingPlugin()
""",
        encoding="utf-8",
    )
    config = AppConfig.from_workspace(tmp_path)
    cache = ChatCache(config.database_path)
    cache.initialize()

    try:
        registry = PluginRegistry()
        registry.load_from_directory(config.plugins_dir)
        tools = registry.mcp_tools(PluginContext(config=config, cache=cache))
    finally:
        cache.close()

    assert tools == ()


def test_plugin_mcp_tool_requires_explicit_read_only_flag(tmp_path):
    plugin_dir = tmp_path / "plugins" / "tooling"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "tooling",
                "module": "plugin.py",
                "factory": "create_plugin",
                "permissions": ["vault.read", "mcp.tools"],
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        """
class ToolingPlugin:
    name = "tooling"
    description = "Tooling plugin"

    def mcp_tools(self, context):
        return [{
            "name": "ambiguous",
            "description": "Missing read_only flag",
            "input_schema": {"type": "object", "properties": {}},
            "handler": self.ambiguous,
        }]

    def ambiguous(self, context, arguments):
        return {"ok": True}

def create_plugin():
    return ToolingPlugin()
""",
        encoding="utf-8",
    )
    config = AppConfig.from_workspace(tmp_path)
    cache = ChatCache(config.database_path)
    cache.initialize()
    audit_path = tmp_path / "logs" / "plugin-audit.jsonl"

    try:
        registry = PluginRegistry(audit_path=audit_path)
        registry.load_from_directory(config.plugins_dir)
        tools = registry.mcp_tools(PluginContext(config=config, cache=cache))
    finally:
        cache.close()

    events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert tools == ()
    assert any(
        event["event"] == "plugin_tool_rejected"
        and event["plugin"] == "tooling"
        and event["reason"] == "missing_read_only_flag"
        for event in events
    )


def test_plugin_tool_audit_uses_real_manifest_path(tmp_path):
    plugin_dir = tmp_path / "plugins" / "tooling"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "tooling",
                "module": "plugin.py",
                "factory": "create_plugin",
                "permissions": ["vault.read"],
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        """
class ToolingPlugin:
    name = "tooling"
    description = "Tooling plugin"

    def mcp_tools(self, context):
        return []

def create_plugin():
    return ToolingPlugin()
""",
        encoding="utf-8",
    )
    config = AppConfig.from_workspace(tmp_path)
    cache = ChatCache(config.database_path)
    cache.initialize()
    audit_path = tmp_path / "logs" / "plugin-audit.jsonl"

    try:
        registry = PluginRegistry(audit_path=audit_path)
        registry.load_from_directory(config.plugins_dir)
        registry.mcp_tools(PluginContext(config=config, cache=cache))
    finally:
        cache.close()

    events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    denied = [
        event
        for event in events
        if event["event"] == "plugin_tool_denied" and event["plugin"] == "tooling"
    ]
    assert denied
    assert denied[-1]["manifest_path"] == str(plugin_dir / "plugin.json")


def test_plugin_mcp_tools_reject_duplicate_external_names(tmp_path):
    plugin_dir = tmp_path / "plugins" / "tooling"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "tooling",
                "module": "plugin.py",
                "factory": "create_plugin",
                "permissions": ["vault.read", "mcp.tools"],
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        """
class ToolPlugin:
    name = "tool"
    description = "Tool plugin"

    def mcp_tools(self, context):
        return [
            {
                "name": "echo",
                "description": "Echo a value",
                "input_schema": {"type": "object", "properties": {}},
                "read_only": True,
                "handler": self.echo,
            },
            {
                "name": "echo",
                "description": "Duplicate echo",
                "input_schema": {"type": "object", "properties": {}},
                "read_only": True,
                "handler": self.echo,
            },
        ]

    def echo(self, context, arguments):
        return {"plugin": self.name}

def create_plugin():
    return ToolPlugin()
""",
        encoding="utf-8",
    )
    config = AppConfig.from_workspace(tmp_path)
    cache = ChatCache(config.database_path)
    cache.initialize()
    audit_path = tmp_path / "logs" / "plugin-audit.jsonl"

    try:
        registry = PluginRegistry(audit_path=audit_path)
        registry.load_from_directory(config.plugins_dir)
        tools = registry.mcp_tools(PluginContext(config=config, cache=cache))
    finally:
        cache.close()

    events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert [tool.external_name for tool in tools] == ["chatalyst_plugin_tooling_echo"]
    assert any(
        event["event"] == "plugin_tool_rejected"
        and event["plugin"] == "tooling"
        and event["reason"] == "duplicate_external_name"
        for event in events
    )


def test_plugin_registry_rejects_duplicate_normalized_plugin_names(tmp_path):
    for plugin_name in ("tool-kit", "tool_kit"):
        plugin_dir = tmp_path / "plugins" / plugin_name
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text(
            json.dumps(
                {
                    "name": plugin_name,
                    "module": "plugin.py",
                    "factory": "create_plugin",
                }
            ),
            encoding="utf-8",
        )
        (plugin_dir / "plugin.py").write_text(
            """
class NamedPlugin:
    name = "named"
    description = "Named plugin"

def create_plugin():
    return NamedPlugin()
""",
            encoding="utf-8",
        )
    audit_path = tmp_path / "logs" / "plugin-audit.jsonl"

    registry = PluginRegistry(audit_path=audit_path)
    registry.load_from_directory(tmp_path / "plugins")

    events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert registry.names == ("tool-kit",)
    assert any(
        event["event"] == "plugin_rejected"
        and event["plugin"] == "tool_kit"
        and event["reason"] == "duplicate_plugin_slug"
        for event in events
    )
