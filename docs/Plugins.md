# Chatalyst Plugins

Chatalyst plugins are local-only Python modules loaded from the workspace
`plugins/` directory. They are intended for integrations such as local file
search, Obsidian, Git/Forgejo context, document search, or private indexing.

Plugins run inside the Chatalyst process, so only install plugins you trust.

## Layout

```text
plugins/
  sample/
    plugin.json
    plugin.py
```

`plugin.json`:

```json
{
  "module": "plugin.py",
  "factory": "create_plugin"
}
```

`plugin.py`:

```python
class SamplePlugin:
    name = "sample"
    description = "Sample local plugin"

    def on_startup(self, context):
        pass

    def on_search_results(self, context, query, results):
        return results


def create_plugin():
    return SamplePlugin()
```

## Hooks

Supported hooks:

- `on_startup(context)`
- `on_conversation_opened(context, conversation_id)`
- `on_message_cached(context, message)`
- `on_search_results(context, query, results)`
- `on_before_export(context, conversation)`

`context.config` exposes workspace paths and runtime settings.
`context.cache` exposes the local SQLite-backed vault API.

## Safety

- Plugin modules must live inside their own plugin folder.
- Plugin manifests cannot load Python files outside that folder.
- Plugin state should stay inside the Chatalyst workspace.
- Do not put secrets in plugin source files; use local ignored config files.
