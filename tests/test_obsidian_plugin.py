from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from chatalyst.core.models import Message, MessageRole


def _plugin():
    plugin_path = Path(__file__).resolve().parents[1] / "plugins" / "obsidian_vault" / "plugin.py"
    spec = importlib.util.spec_from_file_location("obsidian_vault_plugin_test", plugin_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.create_plugin()


def test_obsidian_plugin_capture_request_writes_requirement_note(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("CHATALYST_OBSIDIAN_VAULT", str(vault))
    monkeypatch.setenv("CHATALYST_OBSIDIAN_FOLDER", "Chatalyst/Requirements")
    plugin = _plugin()

    result = plugin.capture_request(
        None,
        {
            "body": "Run the athena-visual-qa-gate before handoff.",
            "title": "Visual QA Gate",
            "conversation_id": "chat-1",
            "message_id": "msg-1",
            "source": "test",
        },
    )

    assert result["captured"] is True
    note = Path(result["path"])
    assert note.exists()
    body = note.read_text(encoding="utf-8")
    assert "athena-visual-qa-gate" in body
    assert "Run the athena-visual-qa-gate before handoff." in body
    assert "conversation_id: \"chat-1\"" in body


def test_obsidian_plugin_capture_request_is_idempotent_for_same_message(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    monkeypatch.setenv("CHATALYST_OBSIDIAN_VAULT", str(vault))
    plugin = _plugin()
    payload = {
        "body": "Run the athena-visual-qa-gate before handoff.",
        "conversation_id": "chat-1",
        "message_id": "msg-1",
    }

    first = plugin.capture_request(None, payload)
    second = plugin.capture_request(None, payload)

    assert first["captured"] is True
    assert second["captured"] is True
    assert second["existing"] is True
    assert first["path"] == second["path"]
    assert len(list(vault.rglob("*.md"))) == 1


def test_obsidian_plugin_status_reports_configuration(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("CHATALYST_OBSIDIAN_VAULT", str(vault))
    monkeypatch.setenv("CHATALYST_OBSIDIAN_REQUIREMENTS", "gate-one,gate-two")
    plugin = _plugin()

    result = plugin.status(None, {})

    assert result["configured"] is True
    assert result["vault_path"] == str(vault.resolve())
    assert result["vault_writable"] is True
    assert result["requirements"] == ["gate-one", "gate-two"]


def test_obsidian_plugin_ignores_messages_without_configured_requirement(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    monkeypatch.setenv("CHATALYST_OBSIDIAN_VAULT", str(vault))
    plugin = _plugin()

    plugin.on_message_cached(
        None,
        Message(
            id="msg-1",
            conversation_id="chat-1",
            role=MessageRole.USER,
            markdown="Normal prompt without configured gate.",
        ),
    )

    assert not list(vault.rglob("*.md"))


def test_obsidian_plugin_message_cached_captures_user_requirement(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    monkeypatch.setenv("CHATALYST_OBSIDIAN_VAULT", str(vault))
    plugin = _plugin()

    plugin.on_message_cached(
        None,
        Message(
            id="msg-1",
            conversation_id="chat-1",
            role=MessageRole.USER,
            markdown="Requirement: athena-visual-qa-gate must be completed.",
        ),
    )

    notes = list(vault.rglob("*.md"))
    assert len(notes) == 1
    assert "Requirement: athena-visual-qa-gate must be completed." in notes[0].read_text(
        encoding="utf-8"
    )


def test_obsidian_plugin_does_not_capture_assistant_requirement_text(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    monkeypatch.setenv("CHATALYST_OBSIDIAN_VAULT", str(vault))
    plugin = _plugin()

    plugin.on_message_cached(
        None,
        Message(
            id="msg-1",
            conversation_id="chat-1",
            role=MessageRole.ASSISTANT,
            markdown="athena-visual-qa-gate",
        ),
    )

    assert not list(vault.rglob("*.md"))
