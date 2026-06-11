# Chatalyst MCP

Chatalyst can run as a local MCP stdio server on any macOS or Linux machine
that can run Python, `uv`, and Playwright Chromium. A Raspberry Pi is only one
possible host; it is not required.

The MCP server uses the same local workspace folders as the TUI:

- `storage/` for the SQLite cache
- `exports/` for exported conversations
- `work/` for staged snippets and local work products
- `profile/chromium/` for the authenticated Chromium profile

Log in once with a visible browser on the same machine:

```bash
uv sync
uv run playwright install chromium
uv run chatalyst --login
```

After login, run MCP mode from the project directory:

```bash
uv run chatalyst --mcp --browser-mode provider
```

Or use the standalone entry point:

```bash
uv run chatalyst-mcp --workspace /path/to/chatalyst --browser-mode provider
```

`provider` mode keeps Chatalyst as a terminal/stdio service and opens Chromium
only as the authenticated ChatGPT provider when live ChatGPT work is needed.
Use `--headless` only after confirming the saved browser session works on that
machine.

Before connecting an MCP client, run a local check:

```bash
uv run chatalyst --doctor --mcp --browser-mode provider
uv run chatalyst --smoke --mcp-read-only --browser-mode provider
uv run chatalyst --project-doctor --mcp-default-project "Research"
```

`doctor` does not open ChatGPT. It checks the workspace, private runtime paths,
cache counts, installed commands, and the MCP tool schema.
`smoke` does not open ChatGPT either. It exercises MCP initialize, tools/list,
and `chatalyst_health`.
`project-doctor` does open ChatGPT. It reports login/browser state, visible
projects, the current URL, and whether a configured project name, project URL,
or project id can be opened.

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
    "--browser-mode",
    "provider",
    "--mcp-default-project",
    "Research",
    "--mcp-live-response-timeout-seconds",
    "180"
  ],
  "env": {}
}
```

For read-only local automation, add `--read-only` to `chatalyst-mcp` or
`--mcp-read-only` to `chatalyst --mcp`. Read-only mode exposes vault inspection
tools without exports, staged snippets, or live ChatGPT sends.

## Health Tool

MCP clients can call `chatalyst_health` before doing live work. By default it
does not start Chromium; it reports workspace paths, configured scope, cached
projects, runtime lock status, cache counts, browser mode/profile, and whether a
default project has cached conversations. Pass `check_browser: true` only when
you intentionally want a live browser/login check.

## Project Scope Proof

When `--mcp-default-project` is set, or when a `project_name` argument is passed,
`chatalyst_send_new_message` opens that ChatGPT project before sending. The value
can be a visible project name, a ChatGPT `/g/...` project URL, or a project id.
The tool response includes a `scope` object with:

- `requested_project`
- `verified`
- `reason`
- `url`

If Chatalyst can send the prompt but cannot prove that the visible ChatGPT page
and the local cache both agree on the project, the response status is
`scope_uncertain`. Treat that as a successful send with a project placement
warning, not as proof that the chat landed in the requested project.

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
