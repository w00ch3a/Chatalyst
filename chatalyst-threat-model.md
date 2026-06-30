# Chatalyst Threat Model

Last updated: 2026-06-28
Release target reviewed: `chatalyst 0.3.3`

## Executive summary

Chatalyst is a local-first ChatGPT automation and knowledge workspace. Its high-value assets are the authenticated browser profile, SQLite conversation vault, exported conversations, snippets, plugin configuration, and project aliases.

The dominant risks are local data disclosure, unsafe MCP exposure, accidental writes through a real ChatGPT session, malicious or overbroad plugins, and executing ChatGPT-derived snippets locally. Chatalyst is not designed as a public internet service. Treat it as owner-local automation unless a separate trusted transport, authentication layer, and host isolation boundary is supplied.

## Product and runtime surfaces

- CLI/TUI entrypoint: `chatalyst` / `chatgpt-tui`.
- MCP stdio server: `chatalyst --mcp` and `chatalyst-mcp`.
- Browser automation: Playwright-controlled Chromium using a real authenticated ChatGPT session.
- Local vault: SQLite under `storage/chat_cache.db`.
- Runtime profile: Chromium profile under `profile/chromium/`.
- Local artifacts: `logs/`, `exports/`, `work/snippets/`, `runtime/`, `config/project_aliases.json`.
- Plugin system: manifest-backed local plugins under workspace or account plugin directories.
- Account isolation: optional `accounts/NAME/` workspace partitioning.

## Assets requiring protection

- Authenticated ChatGPT browser profile and storage state.
- Cached conversations, messages, notes, tags, bookmarks, projects, project URLs, and search indexes.
- Exported Markdown, HTML, JSON, and TXT conversations.
- Staged snippets and any code copied from ChatGPT output.
- Plugin runtime configuration and plugin audit logs.
- Private project aliases and ChatGPT project/app URLs.
- MCP client trust relationship and any wrapper service configuration.

## Trust boundaries

- Human operator to local CLI/TUI.
- MCP client to Chatalyst stdio server.
- Chatalyst service layer to live ChatGPT browser session.
- Browser session to ChatGPT web UI.
- Plugin manifest and plugin code to Chatalyst vault/cache APIs.
- ChatGPT-generated content to terminal/snippet execution.
- Local source tree to public GitHub release artifacts.

## Attacker-controlled or untrusted inputs

- MCP JSON-RPC request bodies, tool arguments, conversation IDs, project names, prompts, and snippet bodies.
- ChatGPT web UI DOM text and selectors, including conversation titles and message markdown.
- Plugin manifests, plugin modules, plugin tool definitions, and plugin outputs.
- Obsidian/plugin configuration values and destination paths.
- Terminal and snippet command text approved by the operator.
- Search strings, tags, notes, and export format selections.
- Local workspace/account names and project alias inputs.

## Security invariants

- Runtime data directories must remain owner-only; sensitive files must be owner-readable only.
- Public release artifacts must not include local profiles, logs, SQLite vaults, exports, snippets, private aliases, tokens, private keys, LAN-only deployment details, or local filesystem paths.
- MCP read-only mode must omit write/live-send tools.
- MCP request sizes must remain bounded.
- Live ChatGPT send/reply operations must route through the service layer, not raw browser control tools.
- Project-scoped live sends must fail closed when the requested project/app cannot be opened or verified.
- Process cleanup must only target Chatalyst MCP processes for the exact requested workspace.
- SQLite writes must preserve referential integrity and use parameterized queries or fixed internal SQL structure.
- Terminal/snippet execution must remain explicit operator action; no automatic execution of model-generated code.
- Plugins must be manifest validated and permission checked before contributing MCP tools.

## Primary threat scenarios

### Local data disclosure

A local user, backup process, sync client, or accidental public commit reads browser state, vault data, logs, exports, snippets, plugin configs, or project aliases. Current controls are `.gitignore` exclusions plus owner-only directory and file modes for runtime data. Release scanning must unpack built artifacts and check source plus wheel/sdist output.

### MCP bridge overexposure

A stdio MCP server is safe only inside the trusted local client boundary. If bridged to a LAN or remote host without authentication and user isolation, another client could read cached data or, in full mode, send prompts through the authenticated ChatGPT session. Current controls include `--mcp-read-only`, bounded request/text sizes, scoped tool schemas, and no raw browser or terminal MCP tools.

### Browser session abuse or drift

The browser profile is an authenticated web session. Selector drift, project visibility changes, or stale links can cause failed sends or wrong-scope attempts. Current controls include project doctor diagnostics, project alias resolution, provider/browser mode support, project scope verification, and explicit failure when requested scope is not visible.

### Plugin abuse

Plugins can observe cache events and, with `mcp.tools`, add MCP tools. A malicious plugin could read or transform vault data, leak content, or misrepresent tool behavior. Current controls include local-only plugin loading, manifest validation, safe slug/module path checks, explicit permission enums, read-only MCP filtering, and audit logs. Residual risk remains because plugin code executes in-process once enabled.

### ChatGPT-derived local code execution

Terminal commands and snippets are intentionally powerful local-operator tools. They avoid shell expansion for `/terminal`, apply timeouts and output caps, and require explicit user action, but approved commands can still modify or exfiltrate local data. This is a by-design risk that should remain visible in release documentation.

### Workspace process control mistakes

Workspace cleanup commands can kill live MCP processes. Current control parses both `--workspace PATH` and `--workspace=PATH`, resolves paths, and only targets processes whose parsed workspace exactly matches the requested workspace.

### Cache integrity regression

Tag reuse and conversation-tag writes must use the real persisted tag ID, not SQLite cursor state after `INSERT OR IGNORE`. Current control fetches the normalized tag ID after insert-ignore and regression tests cover tagging multiple conversations with an existing tag.

## Validated stale files

The following security-relevant files had not been touched in more than three days and were checked against the current release posture on 2026-06-28:

- `chatalyst/core/terminal.py`: still current; no shell execution, timeout and output cap retained.
- `chatalyst/core/snippets.py`: still current; private snippet directory/file modes retained.
- `chatalyst/core/export.py`: still current; private export directory/file modes retained.
- `chatalyst/core/runtime.py`: still current; owner-only lock file and stale-lock logic retained.
- `chatalyst/core/privacy.py`: still current; project reference redaction still used by diagnostics/MCP error paths.
- `chatalyst/core/plugins.py`: still current; manifest validation, permission model, module path containment, and audit logging retained.
- `tests/test_privacy.py`, `tests/test_plugins.py`, `tests/test_terminal.py`: still current for their covered security surfaces.

## Explicit non-goals

- Chatalyst does not replace ChatGPT authentication or account controls.
- Chatalyst does not make a public multi-tenant service safe by itself.
- Chatalyst does not sandbox arbitrary Python or shell snippets.
- Chatalyst does not prevent a fully trusted local operator from exporting or copying their own data.
- Chatalyst does not provide legal, clinical, or compliance advice.

## Release gates for public GitHub

Before public release, verify:

- Source tree excludes generated runtime artifacts.
- Wheel and sdist are rebuilt after cleanup.
- Unpacked wheel and sdist scan clean for private paths, secrets, keys, LAN addresses, vault paths, real email addresses, browser profiles, logs, SQLite vaults, exports, and snippets.
- `ruff`, compile, tests, package build, doctor, and live Pi session test pass for the release source.

## External lane log

- Grok: skipped; no safe configured Grok lane was available in this session.
- Gemini/Antigravity: skipped; no additional value over direct source validation for this documentation refresh.
- Chatalyst: skipped for report authorship to avoid self-referential release evidence. Live Chatalyst runtime proof is recorded in the security practices report.
