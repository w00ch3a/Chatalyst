# Changelog

## 0.2.1 - 2026-06-15

- Added generic private alias resolution inside the ChatGPT browser service, so
  stale ChatGPT App URLs can be mapped to canonical GPT/project URLs without
  hardcoding app-specific routes in source.
- Documented the private alias repair pattern for `/apps/...` to `/g/...`
  compatibility redirects.

## 0.2.0 - 2026-06-15

- Added ChatGPT App URL support for `https://chatgpt.com/apps/...` and
  `https://chat.openai.com/apps/...`, including app launcher handling and
  app-specific scope verification after launch into a conversation.
- Added account-scoped workspaces with isolated Chromium profiles, SQLite
  vaults, project aliases, plugins, logs, exports, runtime locks, and snippets.
- Added private project aliases and redaction helpers so MCP clients can use
  local aliases instead of storing private project URLs in source-controlled
  configuration.
- Added project discovery diagnostics and selector diagnostic packs for safer
  recovery when ChatGPT UI structure changes.
- Added token-frugal MCP mode, prompt budgeting metadata, bounded stdio reads,
  and bounded SQLite reads for recent conversations/messages.
- Hardened plugin loading with explicit permissions, disabled plugin support,
  audit logging, duplicate identity rejection, and duplicate MCP tool rejection.
- Split the lightweight CLI entry point from the Textual TUI to reduce startup
  imports for version checks, smoke checks, and MCP mode.
- Tightened browser resource policy with ChatGPT/OpenAI document host
  restrictions and Chromium background-service reductions.
- Added Apache-2.0 licensing with attribution notice.
- Expanded automated coverage for accounts, browser policy, MCP behavior,
  plugin safety, privacy redaction, and ChatGPT App flows.

## 0.1.0

- Terminal-first ChatGPT knowledge workspace using a real authenticated
  Playwright Chromium session.
- Local SQLite vault with cached conversations, messages, notes, tags,
  bookmarks, snippets, exports, and FTS search.
- MCP stdio server for local automation clients.
- Provider browser mode, ultralight browser profile, project-scoped MCP sends,
  project scope diagnostics, selector diagnostic packs, and runtime lock status.
- Local plugin manifest loader for trusted workspace plugins.
- CLI doctor, project-doctor, smoke, stale-lock repair, and version checks for
  local and remote installs.
