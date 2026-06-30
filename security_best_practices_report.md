# Chatalyst Security Best Practices and Audit Report

Last updated: 2026-06-28
Release target reviewed: `chatalyst 0.3.3`

## Verdict

Release security posture is acceptable for public GitHub publication after the current cleanup and report refresh.

No real PII, secrets, private keys, OpenAI keys, local home paths, vault paths, LAN IPs, browser profiles, logs, SQLite vaults, exports, snippets, or stale release artifacts were present in the final source-plus-artifact privacy scan. The only email-like value observed was the allowed documentation placeholder `user@example.local`.

## Scope

Reviewed and mapped:

- `chatalyst/app.py`
- `chatalyst/core/`
- `chatalyst/widgets/`
- `plugins/obsidian_vault/`
- `tests/`
- `README.md`
- `.gitignore`
- `pyproject.toml`
- `uv.lock`
- release artifacts under `dist/`

Primary surfaces:

- local CLI/TUI
- Playwright browser automation
- SQLite cache/vault
- MCP stdio server
- plugin system
- Obsidian export/plugin support
- local terminal/snippet execution
- public release packaging

## Current controls confirmed

### Runtime privacy

- Runtime directories are created owner-only: `0o700`.
- Sensitive files are written owner-readable only: `0o600`.
- Covered paths include browser profile, SQLite vault, logs, exports, config, plugins, runtime locks, snippets, and account-scoped workspaces.
- `.gitignore` excludes local secrets, environment files, plugin runtime config, browser profiles, SQLite vaults, logs, exports, runtime data, snippets, and generated build/cache artifacts.

### MCP server hardening

- `--mcp-read-only` omits write/live-send tools.
- MCP request lines are capped by `--max-request-bytes`.
- MCP write-text bodies are capped by `--max-text-chars`.
- Tool schemas validate bounded inputs.
- Full mode exposes live ChatGPT send/reply through Chatalyst service methods, not raw browser control.
- Project-scoped sends can use configured defaults or per-call project names and are expected to fail closed when scope is not visible.
- Token-frugal mode limits live result payload size for agent loops.

### Browser/profile safety

- Browser session is treated as sensitive authentication state.
- Provider/browser modes support real logged-in sessions for live Pi operation.
- Project doctor and diagnostics redact project references before reporting.
- Private project aliases are stored in local ignored config instead of committed docs or command lines.

### SQLite/cache safety

- SQLite foreign keys are enabled.
- User-controlled values use parameterized queries or fixed internal SQL construction.
- LIKE search escaping is applied where wildcard semantics would otherwise change query meaning.
- Existing tag reuse now fetches the real normalized tag ID before writing `conversation_tags`, preventing stale `lastrowid` foreign-key failures.

### Plugin safety

- Plugins are local-only and manifest-backed.
- Plugin names and tool names are slug validated.
- Plugin module paths must stay inside the plugin directory.
- MCP tools require explicit `mcp.tools` permission.
- Read-only MCP mode filters non-read-only plugin tools.
- Plugin load/skip/error/tool decisions are written to a private audit log.

### Terminal/snippet controls

- `/terminal` uses argv execution, not shell expansion.
- Pipes, redirects, aliases, and environment expansion are intentionally not interpreted by the terminal runner.
- Terminal commands have timeout and output caps.
- Snippet execution remains explicit user action.
- Shell snippets may be executable; non-shell snippets remain owner-readable only.

## Fixes reflected in this report refresh

### SBP-008: Workspace process cleanup could overmatch MCP processes

- Status: fixed.
- Risk before fix: cleanup commands and runtime shared-workspace cleanup could kill unrelated `chatalyst-mcp` processes.
- Current behavior: process parsing supports both `--workspace PATH` and `--workspace=PATH`, resolves the requested workspace, and only returns processes whose parsed workspace exactly matches.
- Regression coverage: `tests/test_processes.py` covers exact workspace filtering, equals-form workspace arguments, and no-workspace exclusion.

### SBP-009: Existing tag reuse could write the wrong tag ID

- Status: fixed.
- Risk before fix: `INSERT OR IGNORE` plus stale cursor state could produce incorrect tag IDs for `conversation_tags`.
- Current behavior: after insert-ignore, Chatalyst fetches the real tag ID by normalized name and raises if it cannot be found.
- Regression coverage: `tests/test_cache.py` covers applying an existing tag to another conversation.

### SBP-010: Release artifact hygiene needed source-plus-artifact proof

- Status: fixed for this release pass.
- Risk before fix: generated profile/log artifacts and stale `0.3.1` packages existed in the release source directory during pre-release testing.
- Current behavior: generated `logs/` and `profile/` artifacts were removed, stale `dist/` contents were cleaned, and `0.3.2` wheel/sdist were rebuilt.
- Release scan now covers source snapshot plus unpacked wheel and unpacked sdist.

## Validated stale files

These files had not been touched in more than three days but were checked against the current release posture and remain current:

- `chatalyst/core/terminal.py`: safe argv-based execution model, timeout, and output cap still current.
- `chatalyst/core/snippets.py`: private snippet path handling still current.
- `chatalyst/core/export.py`: private export path handling still current.
- `chatalyst/core/runtime.py`: lock ownership and stale-lock behavior still current.
- `chatalyst/core/privacy.py`: project reference redaction still current.
- `chatalyst/core/plugins.py`: plugin manifest and permission model still current.
- `tests/test_privacy.py`: privacy redaction coverage still current.
- `tests/test_plugins.py`: plugin permission/audit behavior coverage still current.
- `tests/test_terminal.py`: terminal runner behavior coverage still current.

## Verification performed for this release pass

On the Pi release source tree:

```bash
uv run ruff check .
uv run python -m compileall -q chatalyst tests
uv run pytest -q
uv build
chatalyst --workspace <runtime-workspace> --doctor --browser-profile lite
```

Results:

- Ruff: passed.
- Compile: passed.
- Tests: `142 passed`.
- Build: `chatalyst-0.3.3.tar.gz` and `chatalyst-0.3.3-py3-none-any.whl` built.
- Doctor: `ok: true`.
- Installed runtime: `chatalyst 0.3.3`, `chatalyst-mcp 0.3.3`.
- Service state: Chatalyst runtime active.
- Live Pi browser-session test: live browser-session smoke completed through the real ChatGPT session.

Release privacy scan:

- Source snapshot scanned.
- Unpacked wheel scanned.
- Unpacked sdist scanned.
- Stale `0.3.1` artifacts removed before final scan.
- Final result: clean, with only allowed placeholder `user@example.local`.

## Residual risks

### Plugin trust remains local-code trust

Plugin manifests and permissions reduce accidental exposure, but enabled plugin Python code still executes in-process. Only install plugins from trusted local sources. Do not add network-enabled plugin behavior without explicit permission review and audit requirements.

### Terminal/snippet execution is intentionally powerful

The terminal and snippet features can run local commands when the operator chooses to do so. This is a user-power feature, not a sandbox. Keep the current explicit-action model and add an optional destructive-command confirmation before broad distribution to less technical users.

### MCP is not a public service

The automation surface is safe only inside a trusted local boundary or a separately secured transport. If exposed to LAN or remote clients, require host firewalling, tunnel authentication, per-user isolation, and read-only mode unless write/live-send behavior is explicitly required.

### Browser session remains the crown jewel

The Chromium profile is equivalent to a logged-in browser. It must stay out of Git, backups with broad readers, shared folders, and support bundles.

## Release recommendations

- Keep `logs/`, `profile/`, `storage/`, `exports/`, `runtime/`, `work/`, account runtime folders, private plugin config, and generated caches excluded from Git.
- Run source-plus-unpacked-artifact privacy scans before every public release.
- Keep `--mcp-read-only` as the default recommendation for bridged automation.
- Add a documented dependency audit gate before tagging future releases.
- Add optional destructive-command confirmation before marketing terminal/snippet execution to broader non-developer users.

## External lane log

- Grok: skipped; no safe configured Grok lane was available in this session.
- Gemini/Antigravity: skipped; no additional value over direct source validation for this documentation refresh.
- Chatalyst: skipped for report authorship to avoid self-referential release evidence. Live Chatalyst runtime proof is recorded above.
