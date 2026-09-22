"""
Public API of the ``questionbank`` module.

Owns the catalog of prepared exam questions: the ``ingest`` module fills it while
building the dataset, and the GUI reads from it to run a session. Every function
takes an open connection so the caller controls the transaction boundary.

Example:
    >>> from api.modules.shared.api import get_connection
    >>> from api.modules.questionbank import api as questionbank
    >>> with get_connection() as conn:
    ...     questionbank.ensure_schema(conn)
    ...     total = questionbank.count(conn)
    ...     picked = questionbank.sample_questions(conn, min(10, total))
"""

from __future__ import annotations

from api.modules.questionbank import repo
from api.modules.questionbank.domain import (
    GRADE_CORRECT,
    GRADE_INCORRECT,
    GRADE_PARTIAL,
    GRADES,
    BrokenMark,
    Question,
)

__all__ = [
    "BrokenMark",
    "Question",
    "GRADES",
    "GRADE_CORRECT",
    "GRADE_PARTIAL",
    "GRADE_INCORRECT",
    "ensure_schema",
    "upsert_question",
    "get_question",
    "list_questions",
    "count",
    "count_available",
    "available_question_ids",
    "is_available",
    "sample_questions",
    "list_unanswered",
    "mark_broken",
    "unmark_broken",
    "clear_broken",
    "list_broken",
    "delete_all_questions",
]

ensure_schema = repo.ensure_schema
upsert_question = repo.upsert
get_question = repo.get
list_questions = repo.list_all
delete_all_questions = repo.delete_all

#: Whole catalog, including questions no session may draw.
count = repo.count

#: Questions a session may draw: the source gave an answer and the user has not
#: flagged the crop. This is the ceiling for a session's size.
count_available = repo.count_available
available_question_ids = repo.available_ids
is_available = repo.is_available
sample_questions = repo.sample

#: Permanently excluded because the source PDF printed no answer.
list_unanswered = repo.list_unanswered

#: The user's badly-cropped flags, managed from the administration screen.
mark_broken = repo.mark_broken
unmark_broken = repo.unmark_broken
clear_broken = repo.clear_broken
list_broken = repo.list_broken
