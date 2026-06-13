# Changelog

## 0.1.0

- Terminal-first ChatGPT knowledge workspace using a real authenticated
  Playwright Chromium session.
- Local SQLite vault with cached conversations, messages, notes, tags,
  bookmarks, snippets, exports, and FTS search.
- MCP stdio server for local automation clients.
- Provider browser mode, ultralight browser profile, project-scoped MCP sends,
  project scope diagnostics, selector diagnostic packs, and runtime lock status.
- Local plugin manifest loader for trusted workspace plugins.
- Account-scoped workspaces with isolated Chromium profiles, SQLite vaults,
  project aliases, plugins, logs, exports, runtime locks, and snippets.
- Advanced plugin manifests with explicit permissions, disabled plugin support,
  audit logging, and namespaced plugin-contributed MCP tools.
- Plugin-contributed MCP tools reject duplicate external tool names so one
  plugin cannot silently overwrite another plugin's handler.
- Plugin loading rejects duplicate normalized plugin identities before hooks run.
- Leaner MCP startup and repeated tool listing by deferring write-only services,
  lazily importing browser/search/export/TUI dependencies, caching the MCP tool
  schema, and avoiding duplicate project conversation scans.
- MCP result paths now use bounded SQLite reads for recent conversations and
  messages, and token-frugal mode adds prompt budgeting plus tighter default
  live result payloads for agent loops.
- MCP stdio reads are bounded before allocation and oversized requests return
  one protocol error before the session closes.
- Browser resource policy now restricts document navigation to ChatGPT/OpenAI
  auth hosts and applies tighter Chromium background, logging, extension, and
  renderer-process limits.
- CLI doctor, project-doctor, smoke, stale-lock repair, and version checks for
  local and remote installs.
- Project-scoped MCP sends can target visible project names, ChatGPT `/g/...`
  project URLs, or project ids.
