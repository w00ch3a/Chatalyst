# Chatalyst Security Best Practices and Audit Report

## Executive summary

Chatalyst is a local-first Python TUI with Playwright, SQLite, and a stdio MCP
server. The most important security posture is protecting the local browser
profile and knowledge vault, keeping MCP safe for LAN-adjacent automation, and
ensuring ChatGPT-derived code only runs after explicit local review. During this
pass, the high-value hardening items were implemented: private filesystem modes,
read-only MCP mode, MCP request/body caps, live send/reply tools routed through
the service layer, and README security guidance.

## Scope

- Reviewed: `chatalyst/app.py`, `chatalyst/core/`, `chatalyst/widgets/`, `README.md`, `.gitignore`,
  `pyproject.toml`, `uv.lock`.
- Stack: Python 3.13+, Textual, Playwright, SQLite, Pydantic, Loguru, Rich,
  markdown-it-py, Pygments, rapidfuzz.
- Relevant guidance: general Python secure coding, local CLI threat boundaries,
  MCP/automation exposure, subprocess safety, secrets/data handling, dependency
  audit hygiene.
- Not applicable: Flask, FastAPI, Django web-server guidance. Chatalyst does not
  expose a web route framework in this repo.

## Fixed during this pass

### SBP-001: Sensitive runtime files depended on process umask

- Severity: High before fix; resolved.
- Location: `chatalyst/core/config.py:83-94`, `chatalyst/core/cache.py:55-63`,
  `chatalyst/core/export.py:40-63`, `chatalyst/core/snippets.py:55-61`, `chatalyst/core/runtime.py:18-32`.
- Evidence: Chatalyst stores ChatGPT session state under `profile/chromium/`,
  conversations in `storage/chat_cache.db`, exports under `exports/`, snippets
  under `work/snippets/`, and runtime locks under `runtime/`.
- Impact: On a permissive umask or shared host, another local user or backup/sync
  process could read sensitive conversations, snippets, exports, logs, or session
  state.
- Fix applied: Runtime directories are now created `0700`; the SQLite vault,
  exports, and lock files are created `0600`; snippets are `0600` unless a shell
  script must be executable.
- Verification: A temporary workspace permission regression confirmed
  `storage_dir`, `profile_dir`, `exports_dir`, `snippets_dir` as `0o700` and
  DB/export/snippet/lock files as `0o600`.
- Residual risk: Existing workspaces created before this change may still need a
  repair command or manual `chmod`.

### SBP-002: MCP live/write surface needed a safer LAN-adjacent posture

- Severity: High if MCP is bridged to LAN; resolved for recommended mode.
- Location: `chatalyst/core/mcp_server.py:34-61`, `chatalyst/core/mcp_server.py:247-289`,
  `chatalyst/app.py:515-573`, `README.md:105-157`.
- Evidence: MCP tools can read the vault and, when full mode is enabled, export
  conversations, stage snippets, create new ChatGPT chats, or reply in existing
  cached conversations through Playwright. New-chat calls can be scoped to a
  visible ChatGPT project by server configuration or per-call `project_name`.
- Impact: If a trusted MCP host is accidentally bridged beyond the local user,
  another LAN client could read cached conversations, create exports/snippets,
  or send messages through the user's authenticated ChatGPT session.
- Fix applied: Added read-only MCP mode via `chatalyst --mcp --mcp-read-only`
  and `chatalyst-mcp --read-only`; write tools are omitted in read-only mode.
  Live new-chat tools now fail closed if a requested project scope is not
  visible in the ChatGPT UI instead of silently creating an unscoped chat.
- Verification: `tools/list` in read-only mode exposes only search/list/get
  tools, and a `chatalyst_stage_snippet` call returns an unknown-tool error.
- Residual risk: A third-party MCP network bridge still needs its own
  authentication and user isolation.

### SBP-003: MCP accepted unbounded request lines and write-tool text bodies

- Severity: Medium before fix; resolved.
- Location: `chatalyst/core/mcp_server.py:116-198`, `chatalyst/core/mcp_server.py:307-344`,
  `chatalyst/app.py:520-530`.
- Evidence: MCP requests are line-delimited JSON from stdin and stage-snippet
  accepts arbitrary text.
- Impact: A buggy or hostile MCP client could send very large inputs and cause
  memory/disk pressure.
- Fix applied: Added default 1 MB per-request cap, bounded search and
  conversation IDs, and default 100k character cap for write-tool text bodies.
- Verification: Oversized request and oversized snippet body smoke tests return
  JSON-RPC errors.
- Residual risk: There is no request-rate limit because MCP is stdio-only. Add
  rate limits if Chatalyst ever grows a network daemon.

### SBP-004: Security guidance was implicit

- Severity: Low before fix; resolved.
- Location: `README.md:147-157`.
- Evidence: Users needed clear guidance that the browser profile and vault are
  secrets and MCP should not be exposed directly to untrusted LAN clients.
- Impact: Misconfiguration risk, especially when running over SSH or with MCP
  automation.
- Fix applied: Added Security Notes describing sensitive paths, private
  permissions, and the `--mcp-read-only` recommendation for LAN-adjacent bridges.

## Findings requiring future work

### SBP-005: Plugin system has no permission model yet

- Severity: Medium when dynamic plugins are implemented.
- Location: `chatalyst/core/plugins.py:12-89`.
- Evidence: `PluginContext` exposes `config` and `cache`; hooks can observe
  startup, conversation opens, cached messages, search results, and exports.
- Impact: A future plugin loader could allow a malicious or overbroad plugin to
  read or modify the vault, leak data, or alter search/export behavior.
- Recommended fix: Before adding dynamic loading, require plugin manifests,
  explicit permissions, disabled-by-default network/file scopes, user approval,
  and audit logs for plugin hook execution.

### SBP-006: Terminal/snippet execution remains intentionally powerful

- Severity: Medium by design.
- Location: `chatalyst/app.py:392-399`, `chatalyst/app.py:466-482`, `chatalyst/core/terminal.py:33-93`,
  `chatalyst/core/snippets.py:71-93`.
- Evidence: Users can run `/terminal ...` commands and execute staged bash or
  Python snippets.
- Existing controls: Commands are parsed with `shlex` and executed without a
  shell; pipes, redirects, aliases, and environment expansion are not performed;
  timeouts and output caps exist.
- Impact: Malicious or mistaken ChatGPT-derived commands can still modify local
  files or exfiltrate data if the user approves execution.
- Recommended fix: Add optional command allowlist/denylist, a second
  confirmation for destructive commands, and a snippet run history panel.

### SBP-007: Dependency audit is manual

- Severity: Low to Medium.
- Location: `pyproject.toml`, `uv.lock`.
- Evidence: Dependencies are locked, and `pip-audit` found no known
  vulnerabilities in the current installed third-party packages, but no CI gate
  exists in the repo.
- Recommended fix: Add a documented release check or CI job for `uvx pip-audit`
  and dependency update review.

## Clean checks

- No OpenAI API keys or private backend API usage found.
- No committed `.env`, private key, token, SQLite DB, browser profile, export,
  log, or snippet artifact found in the clean repo.
- `.gitignore` excludes secrets, profile, vault DBs, logs, exports, runtime, and
  private snippets. Evidence: `.gitignore:20-48`.
- SQLite operations use parameterized queries for user-controlled values; the
  only string-built SQL clauses are internal fixed order/table lists or generated
  placeholder lists.
- MCP full mode exposes constrained live ChatGPT send/reply tools through the
  service layer, with optional project/conversation scoping. It does not expose
  raw browser control or terminal execution.

## Verification performed

```bash
uv run ruff check .
uv run chatalyst --help
uv run chatalyst-mcp --help
uv run chatalyst --mcp --mcp-read-only
uvx pip-audit --path .venv/lib/python3.14/site-packages --progress-spinner off
```

Additional targeted regressions verified:

- private runtime directory and file modes,
- read-only MCP tool list and write-tool rejection,
- oversized MCP request rejection,
- oversized MCP text-body rejection.
