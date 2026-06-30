# Obsidian Vault Plugin

This trusted local plugin captures Chatalyst requests that mention configured
requirement markers such as `athena-visual-qa-gate` into a local Obsidian vault.

Configure with environment variables:

```bash
export CHATALYST_OBSIDIAN_VAULT="$HOME/Documents/Obsidian/My Vault"
export CHATALYST_OBSIDIAN_FOLDER="Chatalyst/Requirements"
export CHATALYST_OBSIDIAN_REQUIREMENTS="athena-visual-qa-gate"
```

Or copy `plugin_config.example.json` to `plugin_config.json` and edit the local
values. Do not commit `plugin_config.json`.

The plugin exposes:

```text
chatalyst_plugin_obsidian_vault_status
chatalyst_plugin_obsidian_vault_capture_request
```

It also listens to cached user messages from MCP live send/reply results and
captures matching requirement-bearing prompts automatically when a vault path is
configured.
