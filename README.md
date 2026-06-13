# Chatalyst

`Chatalyst` is a terminal-first knowledge workspace for ChatGPT. It does not use
OpenAI API keys, private backend APIs, or network reverse engineering. It uses a
real authenticated Chromium session through Playwright, then stores a local
SQLite productivity cache for offline browsing, search, notes, bookmarks, tags,
exports, and future plugins.

## Install

Chatalyst runs on any local macOS or Linux machine that can run Python, `uv`,
and Playwright Chromium. It does not require a Raspberry Pi or appliance-style
host.

```bash
uv sync
uv run playwright install chromium
uv run chatalyst
```

First login opens ChatGPT in Chromium. Log in manually through the real browser.
The browser profile is saved in `profile/chromium/` and reused on later
launches.

```bash
uv run chatalyst --login
```

`--login` keeps the browser visible and waits in the terminal until you press
Enter. It does not ask for, store, or replay credentials; ChatGPT login remains
the normal browser login flow so passkeys, 2FA, CAPTCHA, and OAuth checks still
work correctly.

The Chromium session is optimized for this single job: no extensions, no sync,
no default apps, no background networking helpers, no downloads, no service
workers, muted audio, reduced motion, and request blocking for images, media,
fonts, and common telemetry URLs. ChatGPT page scripts and fetches are left
alone so the browser remains the source of truth.

After a conversation is cached, `Chatalyst` also trims the live Chromium
display: older rendered turns are replaced with tiny local placeholders and old
sidebar items are hidden. This does not alter the ChatGPT conversation itself;
reload or reopen the chat to restore the full browser DOM.

Offline cache mode:

```bash
uv run chatalyst --offline
```

Local health check:

```bash
uv run chatalyst --create-account personal
uv run chatalyst --list-accounts
uv run chatalyst --account personal --login
uv run chatalyst --account personal
uv run chatalyst --doctor
uv run chatalyst --doctor --mcp
uv run chatalyst --smoke --mcp-read-only
uv run chatalyst --project-doctor --mcp-default-project "Research"
uv run chatalyst --set-project-alias work "https://chatgpt.com/g/..."
```

Use `--account NAME` when you want separate OpenAI/ChatGPT identities. Account
mode stores private runtime data under `accounts/NAME/`, including its own
Chromium profile, SQLite vault, project aliases, plugins, logs, exports,
runtime lock, and snippets. Leaving `--account` off preserves the original
single-workspace layout for existing installs.

`--doctor` prints JSON describing the workspace, private runtime paths, cache
counts, installed command paths, and MCP tool schema when `--mcp` is included.
`--smoke` exercises MCP initialize, tools/list, and health without opening
ChatGPT.
`--project-doctor` opens ChatGPT and reports visible projects plus whether a
configured project name, `/g/...` URL, or project id can be opened.
`--set-project-alias` writes a private local alias to
`config/project_aliases.json`, which is ignored by Git. Use aliases such as
`work` or `research` in MCP/TUI config instead of putting private project URLs in
the repository.

Browser lifecycle modes:

```bash
uv run chatalyst --browser-mode auto
uv run chatalyst --browser-mode provider
uv run chatalyst --browser-mode background
uv run chatalyst --browser-mode visible
uv run chatalyst --browser-mode headless
uv run chatalyst --browser-mode sleep
uv run chatalyst --headless
```

`auto` is the default: visible for login, then headless after login is detected.
`provider` is the MCP-first mode: Chatalyst remains a terminal/stdio surface and
uses headed Chromium only as a hidden provider for live ChatGPT work, then closes
it. `background` is kept as an alias for `provider`.
`sleep` closes Chromium between live ChatGPT operations and wakes it only when
needed.
The standard browser profile blocks images, media, fonts, telemetry endpoints,
non-ChatGPT document navigations, audio, pings, background services, extra
extension services, and keeps Chromium to a small renderer process budget.
`ultralight` additionally blocks stylesheets, shrinks the viewport, and prunes
more of the visible ChatGPT DOM for MCP/SSH workloads. Chatalyst intentionally
does not use
`--single-process`; it is unstable for persistent authenticated Chromium
sessions.

For SSH use, log in once on the host with a visible browser, then run:

```bash
uv run chatalyst --workspace /path/to/chatalyst --headless
```

Headless mode reuses the saved `profile/chromium/` session. A fresh login still
needs a visible browser because ChatGPT authentication is human-driven.

## Keyboard

`j` / `k` move chat selection. `Enter` opens a chat. `Tab` switches pane focus.
`/` opens search. `n` creates a new chat. `r` refreshes. `b` opens bookmarks.
`p` or `Ctrl+P` opens the command palette. `Ctrl+B` reveals/restarts the browser
for manual inspection. `Escape` backs out of modal panels such as search. `q`
quits.

Prompt commands:

```text
/search browser sync architecture
/tag #research
/note local note text
/terminal uv run pytest -q
/stage last
/stage bash echo hello
/stage python print("hello")
```

Terminal commands run locally in the project workspace without shell expansion.
Pipes, redirects, aliases, and environment-variable expansion are intentionally
not interpreted in this first pass.

Use `/stage last` to stage the last cached assistant code block. Use `/stage
bash ...`, `/stage python ...`, or `/stage text ...` when you have selected and
pasted a portion of a reply. Staged snippets open in an inspection panel before
you choose copy, save, run, or cancel.

## Local Vault

SQLite data lives at `storage/chat_cache.db`. The schema includes projects,
conversations, messages, notes, tags, conversation tags, bookmarks, exports,
sync state, and `fts_messages` for FTS5 search. Conversations remain readable in
`--offline` after they have been opened and cached.

Exports are written to `exports/` in Markdown, HTML, JSON, or TXT.

## Plugin System

The plugin system is manifest-backed and local-only. Plugins can observe
startup, conversation opens, cached messages, search results, and exports. With
an explicit `mcp.tools` permission, a plugin can also contribute namespaced MCP
tools such as `chatalyst_plugin_localfiles_search`. This reserves space for
local file search, note-vault integration, Git integration, knowledge indexing,
or document search without coupling those integrations to browser automation.

Plugins live in `plugins/` for legacy single-workspace installs, or in
`accounts/NAME/plugins/` when `--account NAME` is used. Plugin load/skip/tool
registration decisions are written to `logs/plugin-audit.jsonl` with owner-only
file permissions.

See [docs/Plugins.md](docs/Plugins.md) for the local plugin manifest format and
a minimal plugin skeleton.

## MCP Server

Chatalyst includes a local stdio MCP server for automation clients. See
[docs/MCP.md](docs/MCP.md) for local macOS/Linux setup notes and client JSON
examples.

```bash
uv run chatalyst-mcp
uv run chatalyst --mcp
uv run chatalyst --account personal --mcp
uv run chatalyst --mcp --browser-mode provider
uv run chatalyst --mcp --browser-mode background
uv run chatalyst --mcp --browser-mode visible
uv run chatalyst --mcp --headless
uv run chatalyst --mcp --mcp-read-only
uv run chatalyst --mcp --offline
uv run chatalyst --mcp --debug
uv run chatalyst --mcp --mcp-live-response-timeout-seconds 180
uv run chatalyst --mcp --mcp-token-frugal
uv run chatalyst --mcp --mcp-default-project "Research"
uv run chatalyst --mcp --mcp-default-conversation "Daily work thread"
```

It exposes the local knowledge vault, not raw browser or terminal control. The
first tool set includes:

- `chatalyst_health`
- `chatalyst_get_scope`
- `chatalyst_prompt_budget`
- `chatalyst_search`
- `chatalyst_list_conversations`
- `chatalyst_list_projects`
- `chatalyst_get_conversation`
- `chatalyst_list_bookmarks`
- `chatalyst_export_conversation`
- `chatalyst_stage_snippet`
- `chatalyst_send_new_message`
- `chatalyst_reply_to_conversation`

The live ChatGPT tools use the same Playwright browser session as the TUI. They
create new chats or reply to cached existing conversations by typing through the
authenticated browser session, then cache the updated conversation locally. They
do not expose raw browser control or terminal execution.

MCP tool choice:

- Use `chatalyst_search`, `chatalyst_list_conversations`, and
  `chatalyst_get_conversation` to inspect the local vault.
- Use `chatalyst_prompt_budget` before large live sends when an agent needs a
  cheap local size check without waking Chromium or spending a ChatGPT turn.
- Use `chatalyst_reply_to_conversation` to continue work or research in an
  existing ChatGPT thread.
- Use `chatalyst_send_new_message` when a fresh ChatGPT thread is the right
  shape for the task.

When `--mcp-default-project` is set, `chatalyst_send_new_message` first opens
that ChatGPT project before creating the new chat. The value can be a visible
project name, a ChatGPT `/g/...` project URL, or a project id. A tool call can
also provide `project_name` to override the configured project for that one
request. If the project cannot be opened, Chatalyst returns an MCP error instead
of silently creating an unscoped chat.

For private projects, prefer local aliases:

```bash
uv run chatalyst --workspace /path/to/workspace \
  --set-project-alias research "https://chatgpt.com/g/..."
```

Then configure MCP with:

```json
"--mcp-default-project", "research"
```

Use `--mcp-read-only` for LAN-adjacent or bridged automation that should not
write exports/snippets or send ChatGPT messages. MCP requests are size-capped by
default; use `--mcp-max-request-bytes` and `--mcp-max-text-chars` only for
trusted local clients that genuinely need larger payloads.

`--offline` also exposes read-only vault tools only. `--debug` enables verbose
diagnostics for selector/browser failures.

The standalone `chatalyst-mcp` entry point is equivalent to MCP stdio mode but
uses its own flag names:

```bash
uv run chatalyst-mcp --workspace /path/to/chatalyst --read-only
uv run chatalyst-mcp --workspace /path/to/chatalyst --account personal
uv run chatalyst-mcp --workspace /path/to/chatalyst --browser-mode provider
uv run chatalyst-mcp --workspace /path/to/chatalyst --mcp-default-project "Research"
uv run chatalyst-mcp --workspace /path/to/chatalyst --mcp-default-conversation "Daily work thread"
uv run chatalyst-mcp --workspace /path/to/chatalyst --max-request-bytes 1000000
uv run chatalyst-mcp --workspace /path/to/chatalyst --max-text-chars 100000
```

Full MCP mode defaults to `provider`: it still uses a real Chromium browser
session because ChatGPT is the source of truth, but Chromium is only a hidden
provider behind the terminal MCP server and is closed after live ChatGPT
operations. Use `--headless` only after confirming the saved ChatGPT session
survives headless launch on that host.

Live send/reply MCP tools wait up to 180 seconds by default for ChatGPT to
produce a new assistant response. Increase `wait_for_response_seconds` per tool
call when the MCP host can tolerate it, or change
`--mcp-live-response-timeout-seconds`. Values up to 900 seconds are accepted for
long reasoning or research turns. If the user message lands but no
assistant response appears before the timeout, MCP returns
`status: submitted_no_response` instead of encouraging duplicate resends.

Use `--mcp-token-frugal` for agent loops that should keep MCP payloads tight.
It lowers the default live result message window from 20 to 6 unless you
explicitly set `--mcp-live-result-message-limit`, and live send/reply results
include `prompt_budget` metadata with character, word, and approximate token
counts. Adjust the local warning threshold with
`--mcp-prompt-warning-tokens`. Common live result and recent conversation reads
ask SQLite only for the bounded window being returned; the full conversation
remains cached locally.

Point MCP clients at the project directory so the server can use the same
`storage/`, `exports/`, and `work/` folders as the TUI:

```json
{
  "mcpServers": {
    "chatalyst": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/chatalyst", "chatalyst", "--mcp"]
    }
  }
}
```

If `chatalyst-mcp` is installed as a user tool, a local MCP client can launch it
directly:

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
    "ultralight",
    "--mcp-default-project",
    "Research",
    "--mcp-live-response-timeout-seconds",
    "180"
  ],
  "env": {}
}
```

For a remote host, use SSH as the MCP command and run the installed command on
that host:

```json
{
  "command": "ssh",
  "args": [
    "user@example.local",
    "/home/user/.local/bin/chatalyst-mcp",
    "--workspace",
    "/home/user/.local/share/chatalyst",
    "--browser-mode",
    "provider"
  ],
  "env": {}
}
```

Each CLI switch and value must be a separate JSON array item. For example,
`"--mcp-default-project", "Research"` is valid; combining the switch and quoted
value into one string is not.

The MCP server can run in full live mode or read-only vault mode. Use the TUI for
first login, manual ChatGPT browser inspection, and reviewed terminal snippet
execution.

`chatalyst_health` includes runtime lock status and cached projects. If an MCP
client appears stuck, run `uv run chatalyst --doctor --mcp` or
`uv run chatalyst --smoke --mcp-read-only` on that host and check
`runtime_lock.owner_pid`. Add `--repair-stale-lock` to remove unlocked lock
metadata whose owner process is already dead.

## Security Notes

Treat `profile/chromium/`, `storage/chat_cache.db`, `exports/`, `logs/`, and
`work/snippets/` as private data. Chatalyst creates runtime data directories with
owner-only permissions and writes the SQLite vault and exports as owner-readable
files.

Do not expose the MCP server directly to untrusted LAN clients. It is a stdio
server intended for local tools, SSH sessions, or a trusted MCP host. If you wrap
it with a network bridge, prefer `--mcp-read-only` unless that bridge is
authenticated and restricted to your account.

## Boundaries

The browser session is the source of truth. The terminal UI is a presentation and
productivity layer. The local database is a cache and knowledge vault.

The legacy `chatgpt-tui` command is also installed as an alias.

## License

Chatalyst is licensed under the Apache License 2.0. You may use, modify, and
redistribute the code, provided you retain the license and attribution notice.

Attribution must reference:

- w00ch3a
- Chatalyst
- https://github.com/w00ch3a/Chatalyst
