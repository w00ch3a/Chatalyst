# Chatalyst MCP

Chatalyst can run as a local MCP stdio server on any macOS or Linux machine
that can run Python, `uv`, and Playwright Chromium. A Raspberry Pi is only one
possible host; it is not required.

The MCP server uses the same local workspace folders as the TUI:

- `storage/` for the SQLite cache
- `exports/` for exported conversations
- `work/` for staged snippets and local work products
- `profile/chromium/` for the authenticated Chromium profile

For multiple OpenAI/ChatGPT accounts, use `--account NAME`. Account mode stores
the same runtime folders under `accounts/NAME/` so each account has its own
Chromium profile, SQLite vault, project aliases, plugins, logs, exports, and
runtime lock.

Log in once with a visible browser on the same machine:

```bash
git clone https://github.com/w00ch3a/Chatalyst.git
cd Chatalyst
uv sync
uv run playwright install chromium
uv run chatalyst --login
uv run chatalyst --account personal --login
```

For an existing local checkout, run `git pull --ff-only` before `uv sync` when
updating to a new release.

After login, run MCP mode from the project directory:

```bash
uv run chatalyst --mcp --browser-mode provider
uv run chatalyst --account personal --mcp --browser-mode provider
```

Or use the standalone entry point:

```bash
uv run chatalyst-mcp --workspace /path/to/chatalyst --browser-mode provider
uv run chatalyst-mcp --workspace /path/to/chatalyst --account personal --browser-mode provider
```

`provider` mode keeps Chatalyst as a terminal/stdio service and opens Chromium
only as the authenticated ChatGPT provider when live ChatGPT work is needed.
Use `--headless` only after confirming the saved browser session works on that
machine.

Before connecting an MCP client, run a local check:

```bash
uv run chatalyst --doctor --mcp --browser-mode provider
uv run chatalyst --account personal --doctor --mcp --browser-mode provider
uv run chatalyst --smoke --mcp-read-only --browser-mode provider
uv run chatalyst --project-doctor --mcp-default-project "Research"
uv run chatalyst --set-project-alias research "https://chatgpt.com/g/..."
```

`doctor` does not open ChatGPT. It checks the workspace, private runtime paths,
cache counts, installed commands, and the MCP tool schema.
`smoke` does not open ChatGPT either. It exercises MCP initialize, tools/list,
and `chatalyst_health`.
`project-doctor` does open ChatGPT. It reports login/browser state, visible
projects, the current URL, and whether a configured project name, project URL,
or project id can be opened.
`set-project-alias` stores a private alias in `config/project_aliases.json`.
That directory is ignored by Git, so MCP clients can pass the alias instead of a
private project URL/id.

## Client Configuration

MCP clients normally launch a command plus an argument array. Keep every switch
and its value in separate `args` rows. Do not combine them into one string.

Correct:

```json
{
  "mcpServers": {
    "chatalyst": {
      "command": "uv",
      "args": [
        "run",
        "--project",
        "/path/to/chatalyst",
        "chatalyst",
        "--mcp",
        "--account",
        "personal",
        "--browser-mode",
        "provider",
        "--mcp-default-project",
        "Research"
      ],
      "env": {}
    }
  }
}
```

Incorrect:

```json
{
  "command": "uv",
  "args": [
    "run",
    "--project /path/to/chatalyst",
    "chatalyst",
    "--mcp",
    "--browser-mode provider"
  ]
}
```

The same split-row rule applies when launching `chatalyst-mcp` directly:

```json
{
  "command": "/home/user/.local/bin/chatalyst-mcp",
  "args": [
    "--workspace",
    "/home/user/.local/share/chatalyst",
    "--account",
    "personal",
    "--browser-mode",
    "provider",
    "--browser-profile",
    "lite",
    "--mcp-token-frugal",
    "--mcp-default-project",
    "Research",
    "--mcp-live-response-timeout-seconds",
    "180"
  ],
  "env": {}
}
```

Requests are read with a bounded stdio line size. If a client sends a JSON-RPC
line larger than `--max-request-bytes`, Chatalyst returns one protocol error and
closes the MCP stdio session instead of continuing to process chunks of the same
oversized request.

Private project aliases:

```bash
uv run chatalyst --workspace /home/user/.local/share/chatalyst \
  --set-project-alias research "https://chatgpt.com/g/g-canonical-gpt-id"
```

Aliases can also map stale ChatGPT App URLs to canonical GPT routes while
keeping private identifiers out of source-controlled MCP config:

```bash
uv run chatalyst --workspace /home/user/.local/share/chatalyst \
  --set-project-alias "https://chatgpt.com/apps/old-app-slug" \
  "https://chatgpt.com/g/g-canonical-gpt-id"
```

```json
{
  "command": "/home/user/.local/bin/chatalyst-mcp",
  "args": [
    "--workspace",
    "/home/user/.local/share/chatalyst",
    "--account",
    "personal",
    "--browser-mode",
    "provider",
    "--mcp-default-project",
    "research"
  ],
  "env": {}
}
```

For read-only local automation, add `--read-only` to `chatalyst-mcp` or
`--mcp-read-only` to `chatalyst --mcp`. Read-only mode exposes vault inspection
tools without exports, staged snippets, or live ChatGPT sends.

Use `--browser-profile lite` for normal live MCP sends/replies. Use
`--browser-profile standard` when launching ChatGPT Apps through
`https://chatgpt.com/apps/...` or `https://chat.openai.com/apps/...`. MCP live
modes now promote `ultralight` to `standard` because ultralight can strip styles
and controls needed by app and project pages.

For long-running agent loops, add `--mcp-token-frugal`. It lowers the default
live result message window from 20 to 6 unless you explicitly pass
`--mcp-live-result-message-limit`, and live send/reply responses include
`prompt_budget` metadata. Use the read-only `chatalyst_prompt_budget` tool when
an agent wants a cheap local prompt-size check before spending a live ChatGPT
turn. Tune the warning threshold with `--mcp-prompt-warning-tokens`.

## Health Tool

MCP clients can call `chatalyst_health` before doing live work. By default it
does not start Chromium; it reports workspace paths, configured scope, cached
projects, account scope, runtime lock status, cache counts, browser
mode/profile, duplicate Chatalyst MCP process status, plugin state, and whether a default project has cached
conversations. It also reports token-frugal mode, the prompt warning threshold,
and the live result message limit. Pass `check_browser: true` only when you
intentionally want a live browser/login check.

## Plugin Tools

Trusted local plugins can contribute MCP tools when their manifest declares the
`mcp.tools` permission. Plugin tools appear in `tools/list` with the prefix:

```text
chatalyst_plugin_<plugin-name>_<tool-name>
```

Read-only MCP mode only exposes read-only plugin tools. Plugin load and tool
registration decisions are recorded in `logs/plugin-audit.jsonl`, or
`accounts/<account>/logs/plugin-audit.jsonl` when `--account` is used.

The bundled `obsidian_vault` plugin adds:

- `chatalyst_plugin_obsidian_vault_status`
- `chatalyst_plugin_obsidian_vault_capture_request`

It can also automatically capture cached user messages that contain configured
requirement markers such as `athena-visual-qa-gate`. See `docs/Plugins.md` for
the plugin manifest, handler contract, and Obsidian setup.

## Project Scope Proof

When `--mcp-default-project` is set, or when a `project_name` argument is passed,
`chatalyst_send_new_message` opens that ChatGPT project or app before sending.
The value can be a visible project name, a ChatGPT `/g/...` project URL, a
ChatGPT `/apps/...` app URL, or a project id.
The tool response includes a `scope` object with:

- `requested_project`
- `verified`
- `reason`
- `url`

If Chatalyst can send the prompt but cannot prove that the visible ChatGPT page
and the local cache both agree on the project, the response status is
`scope_uncertain`. Treat that as a successful send with a project placement
warning, not as proof that the chat landed in the requested project.

Live send tools also accept `image_paths`, an array of up to four local png,
jpg, jpeg, webp, or gif files under 20 MiB each. Chatalyst uploads them through
the authenticated browser session before sending the prompt. It does not store
the image bytes in SQLite.

Live send/reply tools accept `wait_for_response_seconds` values up to 900
seconds for long reasoning or research turns.

## Selector Diagnostics

If a ChatGPT UI selector cannot be resolved, Chatalyst writes a private
diagnostic pack under `logs/selector-failure-*`. Each pack contains the current
URL, page title, attempted selector group, a short visible text sample, and a
screenshot. The directory is created with owner-only permissions.

## Notes

- Chatalyst does not ask for, store, or replay ChatGPT credentials.
- Fresh ChatGPT login requires a visible browser because authentication remains
  the normal browser login flow.
- MCP mode exposes Chatalyst tools over local stdio; it does not expose raw
  browser control or arbitrary terminal execution.
- Use generic project names such as `Research` in shared examples.
