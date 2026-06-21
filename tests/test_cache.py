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


def test_message_upsert_deduplicates_visible_turn_identity(tmp_path):
    cache = ChatCache(tmp_path / "chat_cache.db")
    cache.initialize()
    conversation = Conversation(id="chat-1", title="Chat", sync_status=SyncStatus.CACHED)
    cache.upsert_conversation(conversation)
    try:
        cache.upsert_message(
            Message(
                id="first-generated-id",
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                markdown="streaming text",
                ordinal=1,
            )
        )
        cache.upsert_message(
            Message(
                id="second-generated-id",
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                markdown="final text",
                ordinal=1,
            )
        )
        messages = cache.list_messages(conversation.id)
    finally:
        cache.close()

    assert len(messages) == 1
    assert messages[0].id == "first-generated-id"
    assert messages[0].markdown == "final text"
    assert messages[0].ordinal == 1


def test_conversation_and_message_reads_can_be_bounded_in_sql(tmp_path):
    cache = ChatCache(tmp_path / "chat_cache.db")
    cache.initialize()
    try:
        for index in range(5):
            conversation = Conversation(
                id=f"chat-{index}",
                title=f"Chat {index}",
                sync_status=SyncStatus.CACHED,
            )
            cache.upsert_conversation(conversation)
            cache.upsert_message(
                Message(
                    id=f"msg-{index}",
                    conversation_id=conversation.id,
                    role=MessageRole.USER,
                    markdown=f"message {index}",
                    ordinal=index,
                )
            )

        assert len(cache.list_conversations(limit=2)) == 2
        assert cache.count_messages("chat-4") == 1
        assert cache.list_recent_messages("chat-4", limit=1)[0].markdown == "message 4"
        assert cache.list_recent_messages("chat-4", limit=0) == []
    finally:
        cache.close()


def test_conversation_reference_queries_are_bounded(tmp_path):
    cache = ChatCache(tmp_path / "chat_cache.db")
    cache.initialize()
    try:
        for index in range(4):
            cache.upsert_conversation(
                Conversation(
                    id=f"chat-{index}",
                    title=f"Research Chat {index}",
                    url=f"https://chatgpt.com/c/{index}",
                    chat_identifier=f"identifier-{index}",
                    project_name="Research Lab" if index >= 2 else "Personal",
                    sync_status=SyncStatus.CACHED,
                )
            )

        exact = cache.find_conversation_references("identifier-1", partial=False)
        partial = cache.find_conversation_references("Research Chat", partial=True, limit=2)
        project = cache.find_recent_project_conversation("research")
    finally:
        cache.close()

    assert [conversation.id for conversation in exact] == ["chat-1"]
    assert len(partial) == 2
    assert project is not None
    assert project.project_name == "Research Lab"
