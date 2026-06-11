from __future__ import annotations

import json

from chatalyst.core.plugins import PluginRegistry


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
