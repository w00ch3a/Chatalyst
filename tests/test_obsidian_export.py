from __future__ import annotations

import json

import pytest

from chatalyst.core.config import AppConfig
from chatalyst.core.models import Conversation, Message, MessageRole
from chatalyst.core.obsidian import ObsidianDestinationRequired, ObsidianExportService
from chatalyst.tui_app import parse_obsidian_command


def test_obsidian_export_uses_configured_vault(tmp_path, monkeypatch):
    monkeypatch.delenv("CHATALYST_OBSIDIAN_VAULT", raising=False)
    monkeypatch.delenv("CHATALYST_OBSIDIAN_FOLDER", raising=False)
    config = AppConfig.from_workspace(tmp_path)
    plugin_dir = config.plugins_dir / "obsidian_vault"
    plugin_dir.mkdir(parents=True)
    vault = tmp_path / "vault"
    (plugin_dir / "plugin_config.json").write_text(
        json.dumps({"vault_path": str(vault), "folder": "Inbox/Chatalyst"}),
        encoding="utf-8",
    )
    service = ObsidianExportService(config)
    conversation = Conversation(id="chat-1", title="Terminal Export")
    messages = [
        Message(
            id="msg-1",
            conversation_id="chat-1",
            role=MessageRole.USER,
            markdown="Question",
        ),
        Message(
            id="msg-2",
            conversation_id="chat-1",
            role=MessageRole.ASSISTANT,
            markdown="Answer with ![image](image.png)",
        ),
    ]

    result = service.export_conversation(conversation, messages)

    assert result.used_configured_vault is True
    assert result.destination_type == "vault"
    assert result.path.is_relative_to(vault.resolve())
    body = result.path.read_text(encoding="utf-8")
    assert "Terminal Export" in body
    assert "Answer with ![image](image.png)" in body
    assert "conversation_id: \"chat-1\"" in body
    assert oct(result.path.stat().st_mode & 0o777) == "0o600"


def test_obsidian_export_writes_user_supplied_markdown_file(tmp_path, monkeypatch):
    monkeypatch.delenv("CHATALYST_OBSIDIAN_VAULT", raising=False)
    config = AppConfig.from_workspace(tmp_path)
    service = ObsidianExportService(config)
    target = tmp_path / "handoff.md"

    result = service.export_markdown(
        title="Manual Export",
        body="# Manual Export\n\nSelected reply text",
        destination=target,
        source="test",
    )

    assert result.path == target.resolve()
    assert result.destination_type == "markdown_file"
    assert result.used_configured_vault is False
    assert "Selected reply text" in target.read_text(encoding="utf-8")


def test_obsidian_export_does_not_overwrite_existing_markdown_file(tmp_path, monkeypatch):
    monkeypatch.delenv("CHATALYST_OBSIDIAN_VAULT", raising=False)
    config = AppConfig.from_workspace(tmp_path)
    service = ObsidianExportService(config)
    target = tmp_path / "handoff.md"
    target.write_text("existing", encoding="utf-8")

    result = service.export_markdown(
        title="Manual Export",
        body="new body",
        destination=target,
    )

    assert target.read_text(encoding="utf-8") == "existing"
    assert result.path == tmp_path / "handoff-2.md"
    assert "new body" in result.path.read_text(encoding="utf-8")


def test_obsidian_export_preserves_existing_parent_directory_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("CHATALYST_OBSIDIAN_VAULT", raising=False)
    config = AppConfig.from_workspace(tmp_path)
    service = ObsidianExportService(config)
    parent = tmp_path / "existing"
    parent.mkdir()
    parent.chmod(0o755)

    service.export_markdown(
        title="Manual Export",
        body="body",
        destination=parent / "handoff.md",
    )

    assert oct(parent.stat().st_mode & 0o777) == "0o755"


def test_obsidian_export_requires_destination_without_configured_vault(tmp_path, monkeypatch):
    monkeypatch.delenv("CHATALYST_OBSIDIAN_VAULT", raising=False)
    config = AppConfig.from_workspace(tmp_path)
    service = ObsidianExportService(config)

    with pytest.raises(ObsidianDestinationRequired):
        service.export_markdown(title="Missing", body="body")


def test_obsidian_export_directory_destination_is_treated_as_vault(tmp_path, monkeypatch):
    monkeypatch.delenv("CHATALYST_OBSIDIAN_VAULT", raising=False)
    monkeypatch.setenv("CHATALYST_OBSIDIAN_FOLDER", "Captured")
    config = AppConfig.from_workspace(tmp_path)
    service = ObsidianExportService(config)
    vault = tmp_path / "provided-vault"

    result = service.export_markdown(
        title="Provided Vault",
        body="visible markdown",
        destination=vault,
    )

    assert result.destination_type == "vault"
    assert result.path.parent == vault.resolve() / "Captured"
    assert "visible markdown" in result.path.read_text(encoding="utf-8")


def test_parse_obsidian_command_variants():
    assert parse_obsidian_command("/ov").selection == "conversation"
    assert parse_obsidian_command("/obsidian").selection == "conversation"
    last = parse_obsidian_command("/ov last ~/Desktop/reply.md")
    assert last.selection == "last"
    assert last.destination == "~/Desktop/reply.md"
    visible = parse_obsidian_command("/ov visible ~/Vault")
    assert visible.selection == "visible"
    assert visible.destination == "~/Vault"
    text = parse_obsidian_command("/ov text selected **markdown**")
    assert text.selection == "text"
    assert text.body == "selected **markdown**"
    destination = parse_obsidian_command("/ov ~/Desktop/chat.md")
    assert destination.selection == "conversation"
    assert destination.destination == "~/Desktop/chat.md"
