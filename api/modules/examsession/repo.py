"""
Persistence for exam sessions, their grades and the application settings.

This module owns the ``sessions``, ``session_answers`` and ``app_settings``
tables. It is pure storage: it never decides whether an answer still counts,
because that depends on the question catalog, which belongs to another module.
:mod:`api.modules.examsession.services` joins the two.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from api.modules.examsession.domain import (
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    ExamSession,
)
from api.modules.shared.api import apply_migrations

#: Component name used to version this module's schema independently.
_COMPONENT = "examsession"

_MIGRATIONS: list[tuple[int, list[str]]] = [
    (
        1,
        [
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at    TEXT    NOT NULL,
                finished_at   TEXT,
                planned_count INTEGER NOT NULL,
                status        TEXT    NOT NULL
            )
            """,
            # question_id deliberately carries no FOREIGN KEY: the catalog is
            # rebuilt from the PDFs and may be wiped in the process, while the
            # user's history must outlive that. Question numbers are stable.
            """
            CREATE TABLE IF NOT EXISTS session_answers (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                question_id INTEGER NOT NULL,
                position    INTEGER NOT NULL,
                grade       TEXT    NOT NULL,
                answered_at TEXT    NOT NULL,
                UNIQUE(session_id, question_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_answers_session ON session_answers(session_id)",
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """,
        ],
    ),
    (
        2,
        [
            # The desktop build keeps the drawn question order in the
            # SessionRunner instance for the process's lifetime, which is fine
            # for a single long-lived Tk process. A web request is stateless
            # and a mobile browser can drop its tab at any time, so the plan
            # itself must be durable: this table is what lets a session be
            # resumed after a reload, a tab switch, or a pod restart.
            # question_id deliberately carries no FOREIGN KEY, for the same
            # reason as session_answers.question_id above.
            """
            CREATE TABLE IF NOT EXISTS session_questions (
                session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                position    INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                PRIMARY KEY (session_id, position)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_session_questions_session ON session_questions(session_id)",
        ],
    ),
]


def _now() -> str:
    """Current UTC time as an ISO-8601 string, to the second."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(conn: sqlite3.Connection) -> int:
    """
    Create or upgrade the session tables.

    Args:
        conn: Open connection from ``shared.api.get_connection``.

    Returns:
        Schema version in effect after the call.
    """
    return apply_migrations(conn, _COMPONENT, _MIGRATIONS)


def _row_to_session(row: sqlite3.Row) -> ExamSession:
    """Map a database row onto an :class:`ExamSession`."""
    return ExamSession(
        id=row["id"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        planned_count=row["planned_count"],
        status=row["status"],
    )


def create_session(conn: sqlite3.Connection, planned_count: int) -> ExamSession:
    """
    Open a new session.

    Args:
        conn: Open connection.
        planned_count: How many questions the user asked for.

    Returns:
        The stored session, with its assigned id.
    """
    cursor = conn.execute(
        "INSERT INTO sessions (started_at, finished_at, planned_count, status) "
        "VALUES (?, NULL, ?, ?)",
        (_now(), planned_count, STATUS_IN_PROGRESS),
    )
    conn.commit()

    session = get_session(conn, cursor.lastrowid)
    assert session is not None, "the row was just inserted"
    return session


def get_session(conn: sqlite3.Connection, session_id: int) -> ExamSession | None:
    """
    Fetch one session.

    Returns:
        The session, or ``None`` when there is no such id.
    """
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return _row_to_session(row) if row else None


def list_sessions(conn: sqlite3.Connection) -> list[ExamSession]:
    """Return every session, newest first - the order the results list shows."""
    rows = conn.execute("SELECT * FROM sessions ORDER BY id DESC").fetchall()
    return [_row_to_session(row) for row in rows]


def get_active_session(conn: sqlite3.Connection) -> ExamSession | None:
    """
    Return the most recent session still in progress, if any.

    Used on the web menu screen to offer resuming an interrupted exam instead
    of silently abandoning it. At most one session is ever ``in_progress`` at a
    time in normal use, since starting a new one only happens after the
    previous session finished or was abandoned.
    """
    row = conn.execute(
        "SELECT * FROM sessions WHERE status = ? ORDER BY id DESC LIMIT 1",
        (STATUS_IN_PROGRESS,),
    ).fetchone()
    return _row_to_session(row) if row else None


def insert_plan(conn: sqlite3.Connection, session_id: int, question_ids: list[int]) -> None:
    """
    Store the freshly drawn question order for a new session.

    Args:
        conn: Open connection.
        session_id: Session the plan belongs to.
        question_ids: Question numbers in the order they will be shown,
            one-based position assigned by list order.
    """
    conn.executemany(
        "INSERT INTO session_questions (session_id, position, question_id) VALUES (?, ?, ?)",
        [(session_id, position, qid) for position, qid in enumerate(question_ids, start=1)],
    )
    conn.commit()


def list_plan(conn: sqlite3.Connection, session_id: int) -> list[int]:
    """Return one session's drawn question numbers, in position order."""
    rows = conn.execute(
        "SELECT question_id FROM session_questions WHERE session_id = ? ORDER BY position",
        (session_id,),
    ).fetchall()
    return [row["question_id"] for row in rows]


def replace_plan_question(
    conn: sqlite3.Connection, session_id: int, position: int, question_id: int
) -> None:
    """Swap the question stored at one position, keeping every other position."""
    conn.execute(
        "UPDATE session_questions SET question_id = ? WHERE session_id = ? AND position = ?",
        (question_id, session_id, position),
    )
    conn.commit()


def remove_plan_position(conn: sqlite3.Connection, session_id: int, position: int) -> None:
    """
    Drop one position from the plan and shift every later position down by one.

    Mirrors ``list.pop(index)`` on the in-memory plan: used when a flagged
    question has no replacement left, so the session ends one question shorter
    instead of showing a gap.
    """
    conn.execute(
        "DELETE FROM session_questions WHERE session_id = ? AND position = ?",
        (session_id, position),
    )
    conn.execute(
        "UPDATE session_questions SET position = position - 1 "
        "WHERE session_id = ? AND position > ?",
        (session_id, position),
    )
    conn.commit()


def finish_session(
    conn: sqlite3.Connection, session_id: int, status: str = STATUS_COMPLETED
) -> None:
    """
    Close a session.

    Args:
        conn: Open connection.
        session_id: Session to close.
        status: Final status, normally ``completed`` or ``abandoned``.
    """
    conn.execute(
        "UPDATE sessions SET finished_at = ?, status = ? WHERE id = ?",
        (_now(), status, session_id),
    )
    conn.commit()


def record_grade(
    conn: sqlite3.Connection,
    session_id: int,
    question_id: int,
    position: int,
    grade: str,
) -> None:
    """
    Store the grade the user gave themselves for one question.

    Written as soon as the user grades, so an interrupted session keeps whatever
    was answered. Re-grading the same question in the same session replaces the
    previous value rather than adding a second row.

    Args:
        conn: Open connection.
        session_id: Session the answer belongs to.
        question_id: Question that was shown.
        position: One-based place in the session's order.
        grade: One of the values in ``questionbank.api.GRADES``.
    """
    conn.execute(
        """
        INSERT INTO session_answers (session_id, question_id, position, grade, answered_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(session_id, question_id) DO UPDATE SET
            position    = excluded.position,
            grade       = excluded.grade,
            answered_at = excluded.answered_at
        """,
        (session_id, question_id, position, grade, _now()),
    )
    conn.commit()


def list_answers(conn: sqlite3.Connection, session_id: int) -> list[sqlite3.Row]:
    """Return one session's stored grades in the order they were shown."""
    return conn.execute(
        "SELECT question_id, position, grade, answered_at FROM session_answers "
        "WHERE session_id = ? ORDER BY position",
        (session_id,),
    ).fetchall()


def list_all_answers(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return every stored grade, for the overall statistics."""
    return conn.execute(
        "SELECT session_id, question_id, grade FROM session_answers"
    ).fetchall()


def delete_session(conn: sqlite3.Connection, session_id: int) -> bool:
    """
    Delete one session and its grades.

    The grades go with it through ``ON DELETE CASCADE``, which needs foreign keys
    enabled - ``shared.api.get_connection`` does that per connection.

    Returns:
        ``True`` if a session was deleted.
    """
    cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()
    return cursor.rowcount > 0


def delete_all_sessions(conn: sqlite3.Connection) -> int:
    """
    Delete every session and grade.

    The question catalog and the user's badly-cropped flags are untouched.

    Returns:
        Number of sessions deleted.
    """
    cursor = conn.execute("DELETE FROM sessions")
    conn.commit()
    return cursor.rowcount


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    """Read one setting, or ``None`` when it has never been set."""
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Store one setting, replacing any previous value."""
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


# code-documentation-2026-09-06T09:40:00
