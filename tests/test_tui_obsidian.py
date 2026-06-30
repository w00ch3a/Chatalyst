from __future__ import annotations

from types import SimpleNamespace

import pytest

from chatalyst.core.cache import ChatCache
from chatalyst.core.config import AppConfig
from chatalyst.core.models import Conversation, Message, MessageRole, SyncStatus
from chatalyst.tui_app import ChatGPTTUI
from chatalyst.widgets.command_palette import CommandPalette
from chatalyst.widgets.obsidian_dialog import ObsidianExportDialog


def _seed_conversation(config: AppConfig) -> None:
    cache = ChatCache(config.database_path)
    cache.initialize()
    try:
        conversation = Conversation(
            id="chat-1",
            title="TUI Obsidian Smoke",
            sync_status=SyncStatus.CACHED,
        )
        cache.upsert_conversation(conversation)
        cache.upsert_message(
            Message(
                id="user-1",
                conversation_id=conversation.id,
                role=MessageRole.USER,
                markdown="Question",
                ordinal=0,
            )
        )
        cache.upsert_message(
            Message(
                id="assistant-1",
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                markdown="Reply with ![image](image.png)",
                ordinal=1,
            )
        )
    finally:
        cache.close()


def _submitted(value: str) -> SimpleNamespace:
    return SimpleNamespace(value=value, input=SimpleNamespace(value=value))


@pytest.mark.asyncio
async def test_tui_ov_last_slash_command_exports_latest_reply(tmp_path, monkeypatch):
    monkeypatch.delenv("CHATALYST_OBSIDIAN_VAULT", raising=False)
    config = AppConfig.from_workspace(tmp_path, offline=True)
    _seed_conversation(config)
    target = tmp_path / "reply.md"
    app = ChatGPTTUI(config)

    async with app.run_test() as pilot:
        await pilot.pause()
        await app.on_input_submitted(_submitted(f"/ov last {target}"))
        await pilot.pause()

    body = target.read_text(encoding="utf-8")
    assert "Reply with ![image](image.png)" in body
    assert "message_id: \"assistant-1\"" in body


@pytest.mark.asyncio
async def test_tui_ov_base_slash_command_exports_current_conversation(tmp_path, monkeypatch):
    monkeypatch.delenv("CHATALYST_OBSIDIAN_VAULT", raising=False)
    config = AppConfig.from_workspace(tmp_path, offline=True)
    _seed_conversation(config)
    target = tmp_path / "conversation.md"
    app = ChatGPTTUI(config)

    async with app.run_test() as pilot:
        await pilot.pause()
        await app.on_input_submitted(_submitted(f"/ov {target}"))
        await pilot.pause()

    body = target.read_text(encoding="utf-8")
    assert "# TUI Obsidian Smoke" in body
    assert "## User" in body
    assert "## ChatGPT" in body


@pytest.mark.asyncio
async def test_tui_ov_visible_exports_rendered_pane(tmp_path, monkeypatch):
    monkeypatch.delenv("CHATALYST_OBSIDIAN_VAULT", raising=False)
    config = AppConfig.from_workspace(tmp_path, offline=True)
    _seed_conversation(config)
    target = tmp_path / "visible.md"
    app = ChatGPTTUI(config)

    async with app.run_test() as pilot:
        await pilot.pause()
        await app.on_input_submitted(_submitted(f"/ov visible {target}"))
        await pilot.pause()

    body = target.read_text(encoding="utf-8")
    assert "**User:**" in body
    assert "Reply with ![image](image.png)" in body


@pytest.mark.asyncio
async def test_tui_ov_uses_configured_vault_without_prompt(tmp_path, monkeypatch):
    monkeypatch.delenv("CHATALYST_OBSIDIAN_VAULT", raising=False)
    config = AppConfig.from_workspace(tmp_path, offline=True)
    _seed_conversation(config)
    vault = tmp_path / "vault"
    plugin_dir = config.plugins_dir / "obsidian_vault"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin_config.json").write_text(
        '{"vault_path": "' + str(vault) + '", "folder": "TUI"}',
        encoding="utf-8",
    )
    app = ChatGPTTUI(config)
    prompts: list[object] = []

    async with app.run_test() as pilot:
        await pilot.pause()

        def fake_push_screen(screen, callback=None):
            prompts.append(screen)

        monkeypatch.setattr(app, "push_screen", fake_push_screen)
        await app.on_input_submitted(_submitted("/ov last"))
        await pilot.pause()

    assert prompts == []
    notes = list((vault / "TUI").glob("*.md"))
    assert len(notes) == 1
    body = notes[0].read_text(encoding="utf-8")
    assert "Reply with ![image](image.png)" in body
    assert "message_id: \"assistant-1\"" in body


@pytest.mark.asyncio
async def test_tui_ov_text_prompts_for_destination_when_vault_missing(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("CHATALYST_OBSIDIAN_VAULT", raising=False)
    config = AppConfig.from_workspace(tmp_path, offline=True)
    _seed_conversation(config)
    target = tmp_path / "selected.md"
    app = ChatGPTTUI(config)
    prompted: list[ObsidianExportDialog] = []

    async with app.run_test() as pilot:
        await pilot.pause()

        def fake_push_screen(screen, callback=None):
            assert isinstance(screen, ObsidianExportDialog)
            prompted.append(screen)
            if callback is not None:
                callback(str(target))

        monkeypatch.setattr(app, "push_screen", fake_push_screen)
        await app.on_input_submitted(_submitted("/ov text selected **markdown**"))
        await pilot.pause()

    assert prompted
    assert "selected **markdown**" in target.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_tui_ov_text_prompt_accepts_vault_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("CHATALYST_OBSIDIAN_VAULT", raising=False)
    config = AppConfig.from_workspace(tmp_path, offline=True)
    _seed_conversation(config)
    vault = tmp_path / "prompt-vault"
    app = ChatGPTTUI(config)

    async with app.run_test() as pilot:
        await pilot.pause()

        def fake_push_screen(screen, callback=None):
            assert isinstance(screen, ObsidianExportDialog)
            if callback is not None:
                callback(str(vault))

        monkeypatch.setattr(app, "push_screen", fake_push_screen)
        await app.on_input_submitted(_submitted("/ov text selected **markdown**"))
        await pilot.pause()

    notes = list((vault / "Chatalyst" / "Exports").glob("*.md"))
    assert len(notes) == 1
    assert "selected **markdown**" in notes[0].read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_tui_o_key_binding_executes_obsidian_export(tmp_path, monkeypatch):
    monkeypatch.delenv("CHATALYST_OBSIDIAN_VAULT", raising=False)
    config = AppConfig.from_workspace(tmp_path, offline=True)
    _seed_conversation(config)
    vault = tmp_path / "vault"
    monkeypatch.setenv("CHATALYST_OBSIDIAN_VAULT", str(vault))
    app = ChatGPTTUI(config)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()

    notes = list((vault / "Chatalyst" / "Exports").glob("*.md"))
    assert len(notes) == 1
    body = notes[0].read_text(encoding="utf-8")
    assert "# TUI Obsidian Smoke" in body
    assert "Reply with ![image](image.png)" in body


@pytest.mark.asyncio
async def test_tui_command_palette_obsidian_action_executes_export(tmp_path, monkeypatch):
    monkeypatch.delenv("CHATALYST_OBSIDIAN_VAULT", raising=False)
    config = AppConfig.from_workspace(tmp_path, offline=True)
    _seed_conversation(config)
    vault = tmp_path / "vault"
    monkeypatch.setenv("CHATALYST_OBSIDIAN_VAULT", str(vault))
    app = ChatGPTTUI(config)

    async with app.run_test() as pilot:
        await pilot.pause()
        app._handle_palette("obsidian")  # noqa: SLF001
        await pilot.pause()

    notes = list((vault / "Chatalyst" / "Exports").glob("*.md"))
    assert len(notes) == 1
    assert "Reply with ![image](image.png)" in notes[0].read_text(encoding="utf-8")


def test_tui_obsidian_command_surfaces_are_registered():
    assert ("obsidian", "Export to Obsidian") in CommandPalette.COMMANDS
    assert ("o", "export_obsidian", "Obsidian") in ChatGPTTUI.BINDINGS
