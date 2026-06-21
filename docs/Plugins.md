# Chatalyst Plugins

Chatalyst plugins are trusted, local-only Python modules loaded from a Chatalyst
workspace. They are intended for integrations such as local file search,
Obsidian-style vault search, Git/Forgejo context, private document search, or
local indexing.

Plugins run inside the Chatalyst process. Only install plugins you trust, and do
not put secrets in plugin source files.

## Location

Legacy single-workspace installs load plugins from:

```text
plugins/
```

Account-scoped installs load plugins from that account:

```text
accounts/
  personal/
    plugins/
      localfiles/
        plugin.json
        plugin.py
```

This keeps separate OpenAI/ChatGPT accounts from sharing plugin state or plugin
configuration by accident.

## Bundled Obsidian Vault Plugin

Chatalyst ships with a trusted local plugin at:

```text
plugins/obsidian_vault/
```

When the MCP server is running from a cloned repo, that plugin is available from
the repo workspace. When running an installed tool against another workspace,
copy the folder into that workspace:

```bash
mkdir -p /path/to/chatalyst-workspace/plugins
cp -R /path/to/Chatalyst/plugins/obsidian_vault \
  /path/to/chatalyst-workspace/plugins/
```

Configure the target vault with environment variables:

```bash
export CHATALYST_OBSIDIAN_VAULT="$HOME/Documents/Obsidian/My Vault"
export CHATALYST_OBSIDIAN_FOLDER="Chatalyst/Requirements"
export CHATALYST_OBSIDIAN_REQUIREMENTS="athena-visual-qa-gate"
uv run chatalyst --mcp
```

`CHATALYST_OBSIDIAN_REQUIREMENTS` is a comma-separated list. A cached user
message that contains any configured marker is captured as a Markdown note with
YAML front matter. Assistant messages are ignored by the automatic hook.

The plugin also exposes MCP tools:

- `chatalyst_plugin_obsidian_vault_status`: reports whether the vault path is
  configured and writable.
- `chatalyst_plugin_obsidian_vault_capture_request`: writes a supplied request
  body to the configured vault folder.

Optional local config can be stored in:

```text
plugins/obsidian_vault/plugin_config.json
```

That file is ignored by Git. Do not commit private vault paths, project names,
or requirement routing config. Use
`plugins/obsidian_vault/plugin_config.example.json` as the template.

The TUI can also write directly to Obsidian or a standalone Markdown file:

```text
/ov
/ov last
/ov visible
/ov text selected markdown
/ov ~/Desktop/chat-export.md
```

`/ov` exports the current cached conversation. `/ov last` exports the latest
cached ChatGPT reply. `/ov visible` exports the currently rendered pane. `/ov
text ...` exports pasted markdown. When no vault is configured, the TUI prompts
for either an Obsidian vault folder or an exact `.md` filename.

## Manifest

`plugin.json`:

```json
{
  "name": "localfiles",
  "version": "0.1.0",
  "description": "Search a local notes folder.",
  "module": "plugin.py",
  "factory": "create_plugin",
  "enabled": true,
  "permissions": ["vault.read", "mcp.tools"]
}
```

Fields:

- `name`: local plugin slug used in audit logs and MCP tool names.
- `version`: plugin version string.
- `description`: short human-readable description.
- `module`: Python file inside the plugin folder.
- `factory`: function returning the plugin object. Defaults to `create_plugin`.
- `enabled`: set to `false` to keep a plugin installed but unloaded.
- `permissions`: explicit capability declarations.

Supported permissions:

- `vault.read`: read local cached conversations, messages, notes, tags, and
  bookmarks through `context.cache`.
- `vault.write`: write local vault data or expose write-capable plugin tools.
- `search.extend`: participate in search result hooks.
- `export.observe`: observe export hooks.
- `mcp.tools`: publish plugin MCP tools.
- `network`: plugin may make network calls.
- `terminal`: plugin may run local commands.

Permissions are recorded and checked by Chatalyst for Chatalyst-managed
capabilities such as MCP tool publication. They are not a Python sandbox.

## Hooks

Supported plugin object hooks:

- `on_startup(context)`
- `on_conversation_opened(context, conversation_id)`
- `on_message_cached(context, message)`
- `on_search_results(context, query, results)`
- `on_before_export(context, conversation)`

`context.config` exposes Chatalyst paths and runtime settings.
`context.cache` exposes the local SQLite-backed vault API.

## MCP Tools

Plugins with the `mcp.tools` permission can publish MCP tools. Chatalyst prefixes
the exported name as:

```text
chatalyst_plugin_<plugin-name>_<tool-name>
```

Plugin names are normalized for MCP tool names. For example, `tool-kit` and
`tool_kit` both normalize to `tool_kit`. Chatalyst rejects duplicate normalized
plugin names before plugin hooks run. If one plugin publishes the same external
MCP tool name twice, Chatalyst keeps the first one and rejects the later
duplicate with an audit event.

Example:

```python
class LocalFilesPlugin:
    name = "localfiles"
    description = "Local file search"

    def mcp_tools(self, context):
        return [
            {
                "name": "echo",
                "description": "Echo a value for testing plugin wiring.",
                "input_schema": {
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": {"type": "string"}},
                },
                "read_only": True,
                "handler": self.echo,
            }
        ]

    def echo(self, context, arguments):
        return {"value": arguments["value"]}


def create_plugin():
    return LocalFilesPlugin()
```

This publishes:

```text
chatalyst_plugin_localfiles_echo
```

Plugin MCP handlers receive `(context, arguments)` and must return a JSON object
dictionary. Async handlers are supported. Every plugin MCP tool must explicitly
set `read_only` to either `true` or `false`. Write-capable plugin tools must set
`read_only` to `false` and declare `vault.write`; read-only MCP server mode hides
write-capable plugin tools.

## Audit Log

Plugin load, skip, rejection, and MCP tool registration decisions are appended
to:

```text
logs/plugin-audit.jsonl
```

In account mode that becomes:

```text
accounts/<account>/logs/plugin-audit.jsonl
```

The audit file is written with owner-only permissions.

## Safety Rules

- Plugin modules must live inside their own plugin folder.
- Plugin manifests cannot load Python files outside that folder.
- Disabled plugins are not imported.
- Unknown permissions reject the plugin.
- Duplicate normalized plugin names are rejected before plugin hooks run.
- Plugin MCP tool names must be lower-case snake case.
- Plugin MCP external names must be unique after Chatalyst normalizes the plugin
  name.
- Plugin MCP tools must explicitly declare whether they are read-only.
- Plugin state should stay inside the Chatalyst workspace or account folder.
- Do not expose plugin MCP tools from untrusted plugins to network bridges.
