"""
Persistence for the question catalog and for the user's "badly cropped" flags.

This module owns the ``questions`` and ``broken_questions`` tables: it declares
the DDL, versions it through the shared migration runner, and is the only place
that reads or writes them. Other modules go through
:mod:`api.modules.questionbank.api`.

Both tables together decide which questions an exam session may draw, which is
why the flags live here rather than with the session data: a question is
*available* only when the source printed an answer for it and the user has not
flagged it.
"""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime, timezone

from api.modules.questionbank.domain import BrokenMark, Question
from api.modules.shared.api import apply_migrations

#: Component name used to version this module's schema independently.
_COMPONENT = "questionbank"

_MIGRATIONS: list[tuple[int, list[str]]] = [
    (
        1,
        [
            """
            CREATE TABLE IF NOT EXISTS questions (
                id             INTEGER PRIMARY KEY,
                source_file    TEXT    NOT NULL,
                start_page     INTEGER NOT NULL,
                end_page       INTEGER NOT NULL,
                question_image TEXT    NOT NULL,
                answer_image   TEXT    NOT NULL,
                geometry_json  TEXT    NOT NULL,
                created_at     TEXT    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_questions_source ON questions(source_file)",
        ],
    ),
    (
        2,
        [
            # Whether the source PDF printed an answer. Derived by the build, so
            # a rebuild overwrites it; existing rows default to "has an answer"
            # and are corrected by the next build.
            "ALTER TABLE questions ADD COLUMN has_answer INTEGER NOT NULL DEFAULT 1",
            # Deliberately no FOREIGN KEY to questions: the user's flags must
            # survive both a rebuild and delete_all(). A question number is the
            # stable printed identifier, so a flag left without its row is
            # harmless and comes back into effect when the row returns.
            """
            CREATE TABLE IF NOT EXISTS broken_questions (
                question_id INTEGER PRIMARY KEY,
                marked_at   TEXT NOT NULL
            )
            """,
        ],
    ),
]

#: A question may be drawn for a session only when the source gave an answer and
#: the user has not flagged it as badly cropped. Kept as one string so sampling,
#: counting and the availability check can never drift apart.
_AVAILABLE_PREDICATE = """
    has_answer = 1
    AND id NOT IN (SELECT question_id FROM broken_questions)
"""


def ensure_schema(conn: sqlite3.Connection) -> int:
    """
    Create or upgrade the catalog and flag tables.

    Args:
        conn: Open connection from ``shared.api.get_connection``.

    Returns:
        Schema version in effect after the call.
    """
    return apply_migrations(conn, _COMPONENT, _MIGRATIONS)


def _row_to_question(row: sqlite3.Row) -> Question:
    """Map a database row onto a :class:`Question`."""
    return Question(
        id=row["id"],
        source_file=row["source_file"],
        start_page=row["start_page"],
        end_page=row["end_page"],
        question_image=row["question_image"],
        answer_image=row["answer_image"],
        geometry=json.loads(row["geometry_json"]),
        has_answer=bool(row["has_answer"]),
        created_at=row["created_at"],
    )


def upsert(conn: sqlite3.Connection, question: Question) -> None:
    """
    Insert a question or replace the existing row with the same number.

    Rebuilding the dataset must not create duplicates, and the question number
    printed in the PDF is a natural stable key, so the write is an upsert on
    ``id`` rather than an append.

    Args:
        conn: Open connection.
        question: Question to store. ``created_at`` is overwritten with the
            current UTC time.

    Note:
        Every column is overwritten, ``has_answer`` included, because all of them
        are derived from the source PDF. The user's badly-cropped flags are
        therefore kept in a separate table, which this write does not touch.
    """
    conn.execute(
        """
        INSERT INTO questions (
            id, source_file, start_page, end_page,
            question_image, answer_image, geometry_json, has_answer, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            source_file    = excluded.source_file,
            start_page     = excluded.start_page,
            end_page       = excluded.end_page,
            question_image = excluded.question_image,
            answer_image   = excluded.answer_image,
            geometry_json  = excluded.geometry_json,
            has_answer     = excluded.has_answer,
            created_at     = excluded.created_at
        """,
        (
            question.id,
            question.source_file,
            question.start_page,
            question.end_page,
            question.question_image,
            question.answer_image,
            json.dumps(question.geometry, ensure_ascii=False),
            1 if question.has_answer else 0,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )


def get(conn: sqlite3.Connection, question_id: int) -> Question | None:
    """
    Fetch one question by its number.

    Args:
        conn: Open connection.
        question_id: Question number, 1..370.

    Returns:
        The question, or ``None`` if it is not in the catalog.
    """
    row = conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
    return _row_to_question(row) if row else None


def list_all(conn: sqlite3.Connection) -> list[Question]:
    """
    Return every question ordered by number.

    Used by the build report and by the statistics screens; the catalog is only
    370 rows, so loading it whole is cheaper than paging.
    """
    rows = conn.execute("SELECT * FROM questions ORDER BY id").fetchall()
    return [_row_to_question(row) for row in rows]


def count(conn: sqlite3.Connection) -> int:
    """Return how many questions are in the catalog."""
    return conn.execute("SELECT COUNT(*) AS n FROM questions").fetchone()["n"]


def available_ids(conn: sqlite3.Connection) -> list[int]:
    """
    Return the numbers of every question an exam session may draw.

    Args:
        conn: Open connection.

    Returns:
        Sorted question numbers, excluding those with no answer in the source and
        those the user flagged as badly cropped.
    """
    rows = conn.execute(
        f"SELECT id FROM questions WHERE {_AVAILABLE_PREDICATE} ORDER BY id"
    ).fetchall()
    return [row["id"] for row in rows]


def count_available(conn: sqlite3.Connection) -> int:
    """
    Return how many questions may be drawn for a session.

    This is the ceiling the session-size input must use; :func:`count` reports
    the whole catalog and is larger.
    """
    return conn.execute(
        f"SELECT COUNT(*) AS n FROM questions WHERE {_AVAILABLE_PREDICATE}"
    ).fetchone()["n"]


def is_available(conn: sqlite3.Connection, question_id: int) -> bool:
    """
    Return whether one question may still be drawn and counted.

    Used by the statistics screens: a question flagged after it was answered has
    to drop out of the figures retroactively.
    """
    row = conn.execute(
        f"SELECT 1 FROM questions WHERE id = ? AND {_AVAILABLE_PREDICATE}",
        (question_id,),
    ).fetchone()
    return row is not None


def sample(
    conn: sqlite3.Connection,
    size: int,
    rng: random.Random | None = None,
    exclude: set[int] | None = None,
) -> list[Question]:
    """
    Pick a random set of distinct available questions for an exam session.

    Sampling happens in Python rather than via ``ORDER BY RANDOM()`` so that a
    seeded :class:`random.Random` makes the selection reproducible in tests.

    Args:
        conn: Open connection.
        size: How many questions to draw.
        rng: Random source. Defaults to the module-level generator.
        exclude: Question numbers to keep out of the draw on top of the usual
            availability rules. The exam screen passes the questions already
            shown in the current session, so that replacing a flagged question
            cannot hand back one the user has just seen.

    Returns:
        ``size`` distinct questions in random order.

    Raises:
        ValueError: If ``size`` is below 1 or exceeds how many questions are
            available after exclusions.
    """
    ids = available_ids(conn)
    if exclude:
        ids = [qid for qid in ids if qid not in exclude]

    if size < 1:
        raise ValueError("Session size must be at least 1")
    if size > len(ids):
        raise ValueError(
            f"Requested {size} questions but only {len(ids)} are available"
        )

    chosen = (rng or random).sample(ids, size)
    return [q for q in (get(conn, qid) for qid in chosen) if q is not None]


def list_unanswered(conn: sqlite3.Connection) -> list[Question]:
    """
    Return the questions whose source PDF printed no answer at all.

    These can never be self-graded, so they are permanently out of the draw. The
    administration screen lists them for information only.
    """
    rows = conn.execute(
        "SELECT * FROM questions WHERE has_answer = 0 ORDER BY id"
    ).fetchall()
    return [_row_to_question(row) for row in rows]


def mark_broken(conn: sqlite3.Connection, question_id: int) -> None:
    """
    Flag a question as badly cropped.

    Idempotent: flagging an already flagged question keeps the original
    timestamp, so the administration screen shows when the user first noticed it.

    Args:
        conn: Open connection.
        question_id: Question number to flag.
    """
    conn.execute(
        "INSERT INTO broken_questions (question_id, marked_at) VALUES (?, ?) "
        "ON CONFLICT(question_id) DO NOTHING",
        (question_id, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )


def unmark_broken(conn: sqlite3.Connection, question_id: int) -> bool:
    """
    Remove the badly-cropped flag from one question.

    Args:
        conn: Open connection.
        question_id: Question number to clear.

    Returns:
        ``True`` if a flag was removed, ``False`` if there was none.
    """
    cursor = conn.execute(
        "DELETE FROM broken_questions WHERE question_id = ?", (question_id,)
    )
    return cursor.rowcount > 0


def clear_broken(conn: sqlite3.Connection) -> int:
    """
    Remove every badly-cropped flag.

    Returns:
        Number of flags removed.
    """
    return conn.execute("DELETE FROM broken_questions").rowcount


def list_broken(conn: sqlite3.Connection) -> list[BrokenMark]:
    """
    Return every flagged question with the time it was flagged.

    A flag whose question is no longer in the catalog is skipped: flags outlive
    the catalog by design, and the screen has nothing to show for those.
    """
    rows = conn.execute(
        """
        SELECT q.*, b.marked_at
        FROM broken_questions AS b
        JOIN questions AS q ON q.id = b.question_id
        ORDER BY q.id
        """
    ).fetchall()
    return [
        BrokenMark(question=_row_to_question(row), marked_at=row["marked_at"])
        for row in rows
    ]


def delete_all(conn: sqlite3.Connection) -> int:
    """
    Remove every question from the catalog.

    Only used when rebuilding the dataset from scratch; exam statistics live in
    separate tables and are not touched here.

    Returns:
        Number of rows deleted.
    """
    cursor = conn.execute("DELETE FROM questions")
    return cursor.rowcount


# code-documentation-2026-09-05T18:24:39
