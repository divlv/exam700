"""
Persistence for the model's answers.

This module owns the ``ai_answers`` table. Each row is paid for, so the table is
deliberately independent of everything else: no foreign key to the question
catalog and no cascade from the sessions, which means that neither rebuilding the
catalog nor "reset statistics" can throw an answer away.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from api.modules.aiassist.domain import (
    DIRECTION_ASC,
    DIRECTION_DESC,
    SORT_DATE,
    SORT_QUESTION,
    AiAnswer,
)
from api.modules.shared.api import apply_migrations

#: Component name used to version this module's schema independently.
_COMPONENT = "aiassist"

_MIGRATIONS: list[tuple[int, list[str]]] = [
    (
        1,
        [
            # question_id carries no FOREIGN KEY, like the other per-question
            # tables: the question number is the stable identifier.
            """
            CREATE TABLE IF NOT EXISTS ai_answers (
                question_id    INTEGER PRIMARY KEY,
                html           TEXT    NOT NULL,
                summary        TEXT    NOT NULL DEFAULT '',
                answer         TEXT    NOT NULL DEFAULT '',
                model          TEXT    NOT NULL,
                effort         TEXT    NOT NULL,
                prompt_version INTEGER NOT NULL,
                response_id    TEXT    NOT NULL DEFAULT '',
                input_tokens   INTEGER NOT NULL DEFAULT 0,
                output_tokens  INTEGER NOT NULL DEFAULT 0,
                created_at     TEXT    NOT NULL
            )
            """,
        ],
    ),
]

#: ORDER BY clauses by (sort, direction). Looked up, never built from user input.
_ORDER = {
    (SORT_DATE, DIRECTION_DESC): "created_at DESC, question_id DESC",
    (SORT_DATE, DIRECTION_ASC): "created_at ASC, question_id ASC",
    (SORT_QUESTION, DIRECTION_ASC): "question_id ASC",
    (SORT_QUESTION, DIRECTION_DESC): "question_id DESC",
}


def now() -> str:
    """Current UTC time as an ISO-8601 string, to the second."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(conn: sqlite3.Connection) -> int:
    """Create or upgrade the table. Returns the schema version in effect."""
    return apply_migrations(conn, _COMPONENT, _MIGRATIONS)


def _row_to_answer(row: sqlite3.Row) -> AiAnswer:
    return AiAnswer(
        question_id=row["question_id"],
        html=row["html"],
        summary=row["summary"],
        answer=row["answer"],
        model=row["model"],
        effort=row["effort"],
        prompt_version=row["prompt_version"],
        response_id=row["response_id"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        created_at=row["created_at"],
    )


def get(conn: sqlite3.Connection, question_id: int) -> AiAnswer | None:
    """Return the stored answer for a question, or ``None`` if it was never asked."""
    row = conn.execute(
        "SELECT * FROM ai_answers WHERE question_id = ?", (question_id,)
    ).fetchone()
    return _row_to_answer(row) if row else None


def save(conn: sqlite3.Connection, answer: AiAnswer) -> None:
    """
    Store an answer.

    An answer already stored for the question is never replaced: it was paid
    for, and a second request for the same question is not supposed to happen.
    """
    conn.execute(
        """
        INSERT INTO ai_answers (
            question_id, html, summary, answer, model, effort, prompt_version,
            response_id, input_tokens, output_tokens, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(question_id) DO NOTHING
        """,
        (
            answer.question_id,
            answer.html,
            answer.summary,
            answer.answer,
            answer.model,
            answer.effort,
            answer.prompt_version,
            answer.response_id,
            answer.input_tokens,
            answer.output_tokens,
            answer.created_at,
        ),
    )
    conn.commit()


def list_all(
    conn: sqlite3.Connection, sort: str = SORT_DATE, direction: str = DIRECTION_DESC
) -> list[AiAnswer]:
    """
    Return every stored answer.

    An unknown ``sort`` or ``direction`` falls back to newest first rather than
    failing, since both come straight from the query string.
    """
    order = _ORDER.get((sort, direction), _ORDER[(SORT_DATE, DIRECTION_DESC)])
    rows = conn.execute(f"SELECT * FROM ai_answers ORDER BY {order}").fetchall()
    return [_row_to_answer(row) for row in rows]
