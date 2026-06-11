from __future__ import annotations

import sqlite3

import pytest

from chatalyst.core.cache import ChatCache
from chatalyst.core.models import Conversation, Message, MessageRole, Note, Project, SyncStatus


def test_reconcile_conversation_id_rolls_back_on_mid_update_failure(tmp_path):
    cache = ChatCache(tmp_path / "chat_cache.db")
    cache.initialize()
    old = Conversation(id="old-chat", title="Old", sync_status=SyncStatus.CACHED)
    new = Conversation(id="new-chat", title="New", sync_status=SyncStatus.CACHED)
    cache.upsert_conversation(old)
    cache.upsert_message(
        Message(
            id="msg-1",
            conversation_id=old.id,
            role=MessageRole.USER,
            markdown="hello",
        )
    )
    cache.upsert_note(Note(conversation_id=old.id, body="local note"))
    cache.connection.execute(
        """
        CREATE TEMP TRIGGER fail_note_reconcile
        BEFORE UPDATE ON notes
        BEGIN
            SELECT RAISE(FAIL, 'forced note update failure');
        END;
        """
    )

    try:
        with pytest.raises(sqlite3.IntegrityError, match="forced note update failure"):
            cache.reconcile_conversation_id(old.id, new)

        assert cache.get_conversation(old.id) is not None
        assert cache.get_conversation(new.id) is None
        assert cache.list_messages(old.id)[0].markdown == "hello"
        assert cache.list_messages(new.id) == []
        assert cache.list_notes(old.id)[0].body == "local note"
    finally:
        cache.close()


def test_projects_are_listed_with_urls(tmp_path):
    cache = ChatCache(tmp_path / "chat_cache.db")
    cache.initialize()
    try:
        cache.upsert_project(Project(id="project-2", name="Zulu", url="https://chatgpt.com/g/z"))
        cache.upsert_project(Project(id="project-1", name="Alpha", url="https://chatgpt.com/g/a"))
        projects = cache.list_projects()
    finally:
        cache.close()

    assert [project.name for project in projects] == ["Alpha", "Zulu"]
    assert projects[0].url == "https://chatgpt.com/g/a"
