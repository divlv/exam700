"""
Public API of the ``examsession`` module: run sessions, read statistics, reset.

Owns the user's exam history and preferences. The GUI drives a session through
:class:`SessionRunner` and reads figures through the statistics functions; both
already account for questions that are no longer available, so screens never
have to filter anything themselves.

Example:
    >>> from api.modules.shared.api import get_connection
    >>> from api.modules.examsession import api as examsession
    >>> with get_connection() as conn:
    ...     examsession.ensure_schema(conn)
    ...     runner = examsession.start_session(conn, 10)
    ...     runner.current().id            # doctest: +SKIP
    ...     runner.grade(examsession.GRADE_CORRECT)
    ...     runner.finish()
"""

from __future__ import annotations

from api.modules.examsession.domain import (
    ENTRY_GRADED,
    ENTRY_MARKED,
    ENTRY_PENDING,
    KIND_QUICK,
    KIND_TEST,
    PARTIAL_CREDIT_PERCENT,
    PASS_THRESHOLD_PERCENT,
    STATUS_ABANDONED,
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    AnswerRecord,
    ExamSession,
    Overview,
    PlanEntry,
    SessionDetail,
    SessionSummary,
    Tally,
)
from api.modules.examsession.services import (
    DEFAULT_QUESTION_COUNT,
    DEFAULT_SELECTION_ALGORITHM,
    SELECTION_ADAPTIVE,
    SELECTION_ALGORITHMS,
    SELECTION_RANDOM,
    NoQuestionsAvailable,
    SessionRunner,
    ensure_schema,
    get_active_session,
    get_auto_reveal,
    get_default_question_count,
    get_overview,
    get_selection_algorithm,
    get_session_detail,
    list_sessions,
    reset_all_sessions,
    reset_session,
    resume_session,
    set_auto_reveal,
    set_default_question_count,
    set_selection_algorithm,
    start_quick_question,
    start_session,
)
from api.modules.questionbank.api import (
    GRADE_CORRECT,
    GRADE_INCORRECT,
    GRADE_PARTIAL,
    GRADES,
)

__all__ = [
    "AnswerRecord",
    "DEFAULT_QUESTION_COUNT",
    "DEFAULT_SELECTION_ALGORITHM",
    "ENTRY_GRADED",
    "ENTRY_MARKED",
    "ENTRY_PENDING",
    "ExamSession",
    "KIND_QUICK",
    "KIND_TEST",
    "GRADES",
    "GRADE_CORRECT",
    "GRADE_PARTIAL",
    "GRADE_INCORRECT",
    "NoQuestionsAvailable",
    "Overview",
    "PARTIAL_CREDIT_PERCENT",
    "PlanEntry",
    "PASS_THRESHOLD_PERCENT",
    "SELECTION_ADAPTIVE",
    "SELECTION_ALGORITHMS",
    "SELECTION_RANDOM",
    "STATUS_ABANDONED",
    "STATUS_COMPLETED",
    "STATUS_IN_PROGRESS",
    "SessionDetail",
    "SessionRunner",
    "SessionSummary",
    "Tally",
    "ensure_schema",
    "get_active_session",
    "get_auto_reveal",
    "get_default_question_count",
    "get_overview",
    "get_selection_algorithm",
    "get_session_detail",
    "list_sessions",
    "reset_all_sessions",
    "reset_session",
    "resume_session",
    "set_auto_reveal",
    "set_default_question_count",
    "set_selection_algorithm",
    "start_quick_question",
    "start_session",
]
