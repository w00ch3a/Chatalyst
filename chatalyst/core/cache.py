from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from chatalyst.core.models import (
    Bookmark,
    CodeBlock,
    Conversation,
    ConversationStats,
    ExportRecord,
    Message,
    MessageRole,
    Note,
    Project,
    SearchResult,
    Snippet,
    SnippetRunMode,
    SnippetStatus,
    SyncStatus,
    Tag,
    utc_now,
)


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _row_bool(row: sqlite3.Row, key: str) -> bool:
    return bool(row[key])


class ChatCache:
    """SQLite-backed local knowledge vault."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._connection: sqlite3.Connection | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            self.database_path.parent.chmod(0o700)
            self._connection = sqlite3.connect(self.database_path)
            self.database_path.chmod(0o600)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self.connection
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                url TEXT,
                chat_identifier TEXT,
                project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
                project_name TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_opened_at TEXT,
                most_opened INTEGER NOT NULL DEFAULT 0,
                is_pinned INTEGER NOT NULL DEFAULT 0,
                sync_status TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                markdown TEXT NOT NULL,
                ordinal INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                is_streaming INTEGER NOT NULL DEFAULT 0,
                code_blocks_json TEXT NOT NULL DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversation_tags (
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (conversation_id, tag_id)
            );

            CREATE TABLE IF NOT EXISTS bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                label TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS exports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                format TEXT NOT NULL,
                path TEXT NOT NULL,
                selected_message_ids_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sync_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS snippets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT,
                message_id TEXT,
                body TEXT NOT NULL,
                language TEXT,
                run_mode TEXT NOT NULL,
                path TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS fts_messages USING fts5(
                kind,
                conversation_id UNINDEXED,
                message_id UNINDEXED,
                title,
                body,
                tags,
                notes,
                bookmark_label,
                tokenize = 'porter unicode61'
            );
            """
        )
        conn.commit()

    def upsert_project(self, project: Project) -> None:
        self.connection.execute(
            """
            INSERT INTO projects (id, name, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                updated_at = excluded.updated_at
            """,
            (project.id, project.name, _dt(project.created_at), _dt(project.updated_at)),
        )
        self.connection.commit()

    def upsert_conversation(self, conversation: Conversation) -> None:
        now = utc_now()
        existing = self.get_conversation(conversation.id)
        created_at = conversation.created_at if existing is None else existing.created_at
        updated_at = conversation.updated_at or now
        self.connection.execute(
            """
            INSERT INTO conversations (
                id, title, url, chat_identifier, project_id, project_name, created_at,
                updated_at, last_opened_at, most_opened, is_pinned, sync_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                url = excluded.url,
                chat_identifier = excluded.chat_identifier,
                project_id = excluded.project_id,
                project_name = excluded.project_name,
                updated_at = excluded.updated_at,
                last_opened_at = excluded.last_opened_at,
                most_opened = excluded.most_opened,
                is_pinned = excluded.is_pinned,
                sync_status = excluded.sync_status
            """,
            (
                conversation.id,
                conversation.title,
                conversation.url,
                conversation.chat_identifier,
                conversation.project_id,
                conversation.project_name,
                _dt(created_at),
                _dt(updated_at),
                _dt(conversation.last_opened_at),
                conversation.most_opened,
                int(conversation.is_pinned),
                conversation.sync_status.value,
            ),
        )
        self.connection.commit()
        self.reindex_conversation(conversation.id)

    def reconcile_conversation_id(self, old_id: str, conversation: Conversation) -> Conversation:
        if old_id == conversation.id:
            self.upsert_conversation(conversation)
            return conversation
        existing = self.get_conversation(old_id)
        if existing is None:
            self.upsert_conversation(conversation)
            return conversation
        conn = self.connection
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO conversations (
                    id, title, url, chat_identifier, project_id, project_name, created_at,
                    updated_at, last_opened_at, most_opened, is_pinned, sync_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    chat_identifier = excluded.chat_identifier,
                    project_id = excluded.project_id,
                    project_name = excluded.project_name,
                    updated_at = excluded.updated_at,
                    last_opened_at = excluded.last_opened_at,
                    most_opened = excluded.most_opened,
                    is_pinned = excluded.is_pinned,
                    sync_status = excluded.sync_status
                """,
                (
                    conversation.id,
                    conversation.title,
                    conversation.url,
                    conversation.chat_identifier,
                    conversation.project_id,
                    conversation.project_name,
                    _dt(existing.created_at),
                    _dt(conversation.updated_at),
                    _dt(existing.last_opened_at),
                    existing.most_opened,
                    int(existing.is_pinned),
                    conversation.sync_status.value,
                ),
            )
            for table in (
                "messages",
                "notes",
                "conversation_tags",
                "bookmarks",
                "exports",
                "snippets",
            ):
                conn.execute(
                    f"UPDATE {table} SET conversation_id = ? WHERE conversation_id = ?",
                    (conversation.id, old_id),
                )
            conn.execute("DELETE FROM conversations WHERE id = ?", (old_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        self.reindex_conversation(old_id)
        self.reindex_conversation(conversation.id)
        return conversation

    def mark_opened(self, conversation_id: str) -> None:
        self.connection.execute(
            """
            UPDATE conversations
            SET last_opened_at = ?, most_opened = most_opened + 1
            WHERE id = ?
            """,
            (_dt(utc_now()), conversation_id),
        )
        self.connection.commit()
        self.reindex_conversation(conversation_id)

    def set_pinned(self, conversation_id: str, pinned: bool) -> None:
        self.connection.execute(
            "UPDATE conversations SET is_pinned = ?, updated_at = ? WHERE id = ?",
            (int(pinned), _dt(utc_now()), conversation_id),
        )
        self.connection.commit()
        self.reindex_conversation(conversation_id)

    def upsert_message(self, message: Message) -> None:
        self.connection.execute(
            """
            INSERT INTO messages (
                id, conversation_id, role, markdown, ordinal, created_at, updated_at,
                is_streaming, code_blocks_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                role = excluded.role,
                markdown = excluded.markdown,
                ordinal = excluded.ordinal,
                updated_at = excluded.updated_at,
                is_streaming = excluded.is_streaming,
                code_blocks_json = excluded.code_blocks_json
            """,
            (
                message.id,
                message.conversation_id,
                message.role.value,
                message.markdown,
                message.ordinal,
                _dt(message.created_at),
                _dt(message.updated_at),
                int(message.is_streaming),
                json.dumps([block.model_dump() for block in message.code_blocks]),
            ),
        )
        self.connection.commit()
        self.reindex_conversation(message.conversation_id)

    def upsert_note(self, note: Note) -> int:
        now = utc_now()
        if note.id:
            self.connection.execute(
                "UPDATE notes SET body = ?, updated_at = ? WHERE id = ?",
                (note.body, _dt(now), note.id),
            )
            note_id = int(note.id)
        else:
            cursor = self.connection.execute(
                """
                INSERT INTO notes (conversation_id, body, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (note.conversation_id, note.body, _dt(note.created_at), _dt(note.updated_at)),
            )
            note_id = int(cursor.lastrowid)
        self.connection.commit()
        self.reindex_conversation(note.conversation_id)
        return note_id

    def apply_tag(self, conversation_id: str, tag: Tag) -> int:
        normalized = tag.normalized
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO tags (name, created_at) VALUES (?, ?)",
            (normalized, _dt(tag.created_at)),
        )
        tag_id = int(cursor.lastrowid or self._get_tag_id(normalized))
        self.connection.execute(
            "INSERT OR IGNORE INTO conversation_tags (conversation_id, tag_id) VALUES (?, ?)",
            (conversation_id, tag_id),
        )
        self.connection.commit()
        self.reindex_conversation(conversation_id)
        return tag_id

    def remove_tag(self, conversation_id: str, tag_name: str) -> None:
        tag_id = self._get_tag_id(tag_name.strip().lower().lstrip("#"))
        if tag_id is None:
            return
        self.connection.execute(
            "DELETE FROM conversation_tags WHERE conversation_id = ? AND tag_id = ?",
            (conversation_id, tag_id),
        )
        self.connection.commit()
        self.reindex_conversation(conversation_id)

    def bookmark_message(
        self, conversation_id: str, message_id: str, label: str | None = None
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO bookmarks (conversation_id, message_id, label, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (conversation_id, message_id, label, _dt(utc_now())),
        )
        bookmark_id = int(cursor.lastrowid)
        self.connection.commit()
        self.reindex_conversation(conversation_id)
        return bookmark_id

    def record_export(self, export: ExportRecord) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO exports (
                conversation_id, format, path, selected_message_ids_json, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                export.conversation_id,
                export.format,
                export.path,
                json.dumps(export.selected_message_ids),
                _dt(export.created_at),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def set_sync_state(self, key: str, value: str) -> None:
        self.connection.execute(
            """
            INSERT INTO sync_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, _dt(utc_now())),
        )
        self.connection.commit()

    def create_snippet(self, snippet: Snippet) -> Snippet:
        cursor = self.connection.execute(
            """
            INSERT INTO snippets (
                conversation_id, message_id, body, language, run_mode, path, status,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snippet.conversation_id,
                snippet.message_id,
                snippet.body,
                snippet.language,
                snippet.run_mode.value,
                snippet.path,
                snippet.status.value,
                _dt(snippet.created_at),
                _dt(snippet.updated_at),
            ),
        )
        self.connection.commit()
        return snippet.model_copy(update={"id": str(cursor.lastrowid)})

    def update_snippet(self, snippet: Snippet) -> None:
        if snippet.id is None:
            raise ValueError("Cannot update snippet without id.")
        self.connection.execute(
            """
            UPDATE snippets
            SET body = ?, language = ?, run_mode = ?, path = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                snippet.body,
                snippet.language,
                snippet.run_mode.value,
                snippet.path,
                snippet.status.value,
                _dt(utc_now()),
                snippet.id,
            ),
        )
        self.connection.commit()

    def list_snippets(self, *, limit: int = 20) -> list[Snippet]:
        rows = self.connection.execute(
            "SELECT * FROM snippets ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._snippet_from_row(row) for row in rows]

    def get_sync_state(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM sync_state WHERE key = ?",
            (key,),
        ).fetchone()
        return str(row["value"]) if row else None

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        row = self.connection.execute(
            "SELECT * FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        return self._conversation_from_row(row) if row else None

    def list_conversations(self, *, pinned_first: bool = True) -> list[Conversation]:
        order = "is_pinned DESC, COALESCE(last_opened_at, updated_at) DESC, title COLLATE NOCASE"
        if not pinned_first:
            order = "COALESCE(last_opened_at, updated_at) DESC, title COLLATE NOCASE"
        rows = self.connection.execute(f"SELECT * FROM conversations ORDER BY {order}").fetchall()
        return [self._conversation_from_row(row) for row in rows]

    def list_recent_conversations(self, *, limit: int = 20) -> list[Conversation]:
        rows = self.connection.execute(
            """
            SELECT * FROM conversations
            ORDER BY COALESCE(last_opened_at, updated_at) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [self._conversation_from_row(row) for row in rows]

    def list_messages(
        self, conversation_id: str, selected_message_ids: Iterable[str] | None = None
    ) -> list[Message]:
        selected = list(selected_message_ids) if selected_message_ids is not None else []
        if selected_message_ids is not None and not selected:
            return []
        params: list[Any] = [conversation_id]
        clause = ""
        if selected:
            clause = f" AND id IN ({','.join('?' for _ in selected)})"
            params.extend(selected)
        rows = self.connection.execute(
            f"""
            SELECT * FROM messages
            WHERE conversation_id = ?{clause}
            ORDER BY ordinal, created_at
            """,
            params,
        ).fetchall()
        return [self._message_from_row(row) for row in rows]

    def list_notes(self, conversation_id: str) -> list[Note]:
        rows = self.connection.execute(
            "SELECT * FROM notes WHERE conversation_id = ? ORDER BY updated_at DESC",
            (conversation_id,),
        ).fetchall()
        return [
            Note(
                id=str(row["id"]),
                conversation_id=row["conversation_id"],
                body=row["body"],
                created_at=_parse_dt(row["created_at"]) or utc_now(),
                updated_at=_parse_dt(row["updated_at"]) or utc_now(),
            )
            for row in rows
        ]

    def list_tags(self, conversation_id: str) -> list[Tag]:
        rows = self.connection.execute(
            """
            SELECT tags.* FROM tags
            JOIN conversation_tags ON conversation_tags.tag_id = tags.id
            WHERE conversation_tags.conversation_id = ?
            ORDER BY tags.name
            """,
            (conversation_id,),
        ).fetchall()
        return [
            Tag(
                id=str(row["id"]),
                name=row["name"],
                created_at=_parse_dt(row["created_at"]) or utc_now(),
            )
            for row in rows
        ]

    def list_bookmarks(self, conversation_id: str | None = None) -> list[Bookmark]:
        params: list[Any] = []
        clause = ""
        if conversation_id:
            clause = "WHERE conversation_id = ?"
            params.append(conversation_id)
        rows = self.connection.execute(
            f"SELECT * FROM bookmarks {clause} ORDER BY created_at DESC",
            params,
        ).fetchall()
        return [
            Bookmark(
                id=str(row["id"]),
                conversation_id=row["conversation_id"],
                message_id=row["message_id"],
                label=row["label"],
                created_at=_parse_dt(row["created_at"]) or utc_now(),
            )
            for row in rows
        ]

    def conversation_stats(self, conversation_id: str) -> ConversationStats | None:
        conversation = self.get_conversation(conversation_id)
        if conversation is None:
            return None
        rows = self.list_messages(conversation_id)
        return ConversationStats(
            conversation_id=conversation_id,
            message_count=len(rows),
            word_count=sum(message.word_count for message in rows),
            created_at=conversation.created_at,
            last_updated=conversation.updated_at,
            project_name=conversation.project_name,
            sync_status=conversation.sync_status,
        )

    def reindex_conversation(self, conversation_id: str) -> None:
        self.connection.execute(
            "DELETE FROM fts_messages WHERE conversation_id = ?",
            (conversation_id,),
        )
        conversation = self.get_conversation(conversation_id)
        if conversation is None:
            self.connection.commit()
            return
        tags = " ".join(tag.name for tag in self.list_tags(conversation_id))
        notes = " ".join(note.body for note in self.list_notes(conversation_id))
        bookmarks = self.list_bookmarks(conversation_id)
        bookmark_labels = " ".join(bookmark.label or "" for bookmark in bookmarks)
        self.connection.execute(
            """
            INSERT INTO fts_messages
                (kind, conversation_id, message_id, title, body, tags, notes, bookmark_label)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "conversation",
                conversation_id,
                None,
                conversation.title,
                conversation.project_name or "",
                tags,
                notes,
                bookmark_labels,
            ),
        )
        for message in self.list_messages(conversation_id):
            self.connection.execute(
                """
                INSERT INTO fts_messages
                    (kind, conversation_id, message_id, title, body, tags, notes, bookmark_label)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "message",
                    conversation_id,
                    message.id,
                    conversation.title,
                    message.markdown,
                    tags,
                    notes,
                    bookmark_labels,
                ),
            )
        for bookmark in bookmarks:
            self.connection.execute(
                """
                INSERT INTO fts_messages
                    (kind, conversation_id, message_id, title, body, tags, notes, bookmark_label)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "bookmark",
                    conversation_id,
                    bookmark.message_id,
                    conversation.title,
                    "",
                    tags,
                    notes,
                    bookmark.label or "",
                ),
            )
        self.connection.commit()

    def search_fts(self, query: str, *, limit: int = 50) -> list[SearchResult]:
        if not query.strip():
            return []
        terms = re.findall(r"[\w]+", query, flags=re.UNICODE)
        sanitized = " ".join(f'"{term}"' for term in terms)
        if not sanitized:
            return []
        try:
            rows = self.connection.execute(
                """
                SELECT
                    f.kind,
                    f.conversation_id,
                    f.message_id,
                    f.title,
                    snippet(fts_messages, 4, '[', ']', '...', 16) AS snippet,
                    bm25(fts_messages, 4.0, 1.0, 1.0, 1.3, 1.5, 1.2) AS rank
                FROM fts_messages f
                WHERE fts_messages MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (sanitized, limit),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            logger.warning("FTS search failed for {!r}: {}", query, exc)
            return self._like_search(query, limit=limit)
        return [
            SearchResult(
                conversation_id=row["conversation_id"],
                kind=row["kind"],
                title=row["title"],
                snippet=row["snippet"] or row["title"],
                score=float(-row["rank"] + 100.0),
                message_id=row["message_id"],
            )
            for row in rows
        ]

    def _like_search(self, query: str, *, limit: int) -> list[SearchResult]:
        pattern = f"%{query}%"
        rows = self.connection.execute(
            """
            SELECT
                c.id AS conversation_id,
                c.title,
                m.id AS message_id,
                m.markdown,
                GROUP_CONCAT(DISTINCT t.name) AS tags,
                GROUP_CONCAT(DISTINCT n.body) AS notes,
                GROUP_CONCAT(DISTINCT b.label) AS bookmark_labels
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id = c.id
            LEFT JOIN conversation_tags ct ON ct.conversation_id = c.id
            LEFT JOIN tags t ON t.id = ct.tag_id
            LEFT JOIN notes n ON n.conversation_id = c.id
            LEFT JOIN bookmarks b ON b.conversation_id = c.id
            WHERE c.title LIKE ?
                OR m.markdown LIKE ?
                OR t.name LIKE ?
                OR n.body LIKE ?
                OR b.label LIKE ?
            GROUP BY c.id, c.title, m.id, m.markdown
            LIMIT ?
            """,
            (pattern, pattern, pattern, pattern, pattern, limit),
        ).fetchall()
        return [
            SearchResult(
                conversation_id=row["conversation_id"],
                kind="message" if row["message_id"] else "conversation",
                title=row["title"],
                snippet=(
                    row["markdown"]
                    or row["notes"]
                    or row["bookmark_labels"]
                    or row["tags"]
                    or row["title"]
                )[:240],
                score=1.0,
                message_id=row["message_id"],
            )
            for row in rows
        ]

    def _get_tag_id(self, normalized_name: str) -> int | None:
        row = self.connection.execute(
            "SELECT id FROM tags WHERE name = ?",
            (normalized_name,),
        ).fetchone()
        return int(row["id"]) if row else None

    def _conversation_from_row(self, row: sqlite3.Row) -> Conversation:
        return Conversation(
            id=row["id"],
            title=row["title"],
            url=row["url"],
            chat_identifier=row["chat_identifier"],
            project_id=row["project_id"],
            project_name=row["project_name"],
            created_at=_parse_dt(row["created_at"]) or utc_now(),
            updated_at=_parse_dt(row["updated_at"]) or utc_now(),
            last_opened_at=_parse_dt(row["last_opened_at"]),
            most_opened=int(row["most_opened"]),
            is_pinned=_row_bool(row, "is_pinned"),
            sync_status=SyncStatus(row["sync_status"]),
        )

    def _message_from_row(self, row: sqlite3.Row) -> Message:
        blocks = json.loads(row["code_blocks_json"] or "[]")
        return Message(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=MessageRole(row["role"]),
            markdown=row["markdown"],
            ordinal=int(row["ordinal"]),
            created_at=_parse_dt(row["created_at"]) or utc_now(),
            updated_at=_parse_dt(row["updated_at"]) or utc_now(),
            is_streaming=_row_bool(row, "is_streaming"),
            code_blocks=[CodeBlock(**block) for block in blocks],
        )

    def _snippet_from_row(self, row: sqlite3.Row) -> Snippet:
        return Snippet(
            id=str(row["id"]),
            conversation_id=row["conversation_id"],
            message_id=row["message_id"],
            body=row["body"],
            language=row["language"],
            run_mode=SnippetRunMode(row["run_mode"]),
            path=row["path"],
            status=SnippetStatus(row["status"]),
            created_at=_parse_dt(row["created_at"]) or utc_now(),
            updated_at=_parse_dt(row["updated_at"]) or utc_now(),
        )
