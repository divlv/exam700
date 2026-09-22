"""
Running an exam session and computing its statistics.

Two responsibilities live here, both of which need the question catalog and so
cannot sit in :mod:`api.modules.examsession.repo`:

* :class:`SessionRunner` walks the user through the drawn questions and handles
  flagging a badly cropped one, which draws a replacement so the session keeps
  its requested length.
* The statistics functions decide which stored grades still count. A grade
  counts only while its question is still available, so flagging a question
  removes it from past results too. Availability is read through
  ``questionbank.api``; this module never touches the catalog tables itself.
"""

from __future__ import annotations

import random
import sqlite3

from api.modules.examsession import repo
from api.modules.examsession.domain import (
    STATUS_ABANDONED,
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    AnswerRecord,
    ExamSession,
    Overview,
    SessionDetail,
    SessionSummary,
    Tally,
)
from api.modules.questionbank import api as questionbank
from api.modules.shared.api import get_logger

logger = get_logger(__name__)

#: Setting keys. Defaults apply until the user changes them on the settings screen.
SETTING_DEFAULT_COUNT = "default_question_count"
SETTING_AUTO_REVEAL = "auto_reveal_answer"

DEFAULT_QUESTION_COUNT = 20
DEFAULT_AUTO_REVEAL = False


class NoQuestionsAvailable(RuntimeError):
    """Raised when a session cannot be started because the pool is too small."""


def ensure_schema(conn: sqlite3.Connection) -> int:
    """Create or upgrade the session tables."""
    return repo.ensure_schema(conn)


# ---------------------------------------------------------------------------
# Running a session
# ---------------------------------------------------------------------------


class SessionRunner:
    """
    Walks one session through its questions.

    The runner owns the session's progress so the exam screen stays a thin
    view: it asks for the current question, reports a grade, or reports that the
    question is badly cropped.

    Attributes:
        session: The stored session being played.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        session: ExamSession,
        questions: list[questionbank.Question],
        rng: random.Random | None = None,
    ):
        """
        Args:
            conn: Open connection, used for every write the session makes.
            session: The freshly created session row.
            questions: Questions drawn for it, in the order they will be shown.
            rng: Random source for drawing replacements.
        """
        self._conn = conn
        self._rng = rng
        self.session = session
        self._questions = list(questions)
        self._index = 0
        # Every question this session has drawn, so a replacement can never be
        # one the user has already seen here.
        self._seen: set[int] = {question.id for question in self._questions}
        self._graded = 0
        self._flagged = 0
        self._exhausted = False

    @classmethod
    def _resume(
        cls,
        conn: sqlite3.Connection,
        session: ExamSession,
        questions: list[questionbank.Question],
        graded: int,
        rng: random.Random | None,
    ) -> "SessionRunner":
        """
        Reconstruct a runner from its persisted plan and stored grades.

        Args:
            conn: Open connection.
            session: The in-progress session being resumed.
            questions: The full drawn plan, in position order, as currently
                stored (already reflecting any flag/replace done before the
                process that ran this session last exited).
            graded: How many leading positions already have a stored grade.
                Grading always proceeds in plan order, so this is also the
                index of the first ungraded question.
            rng: Random source for drawing further replacements.

        Note:
            The desktop build's ``flagged`` counter is not persisted - only the
            global broken-question flag is - so it restarts at 0 on resume.
            This affects only the in-session "flagged: N" display, never the
            availability rules or the statistics.
        """
        runner = cls(conn, session, questions, rng)
        runner._index = graded
        runner._graded = graded
        # The stored plan already reflects every replacement made so far, so it
        # doubles as the "ids ever drawn" set; questions that were flagged and
        # swapped out are excluded from future draws globally regardless.
        runner._seen = set(q.id for q in questions)
        return runner

    @property
    def total(self) -> int:
        """How many questions the session will ask - its requested length."""
        return len(self._questions)

    @property
    def position(self) -> int:
        """One-based number of the question being shown."""
        return min(self._index + 1, self.total)

    @property
    def graded(self) -> int:
        """How many questions have been graded so far."""
        return self._graded

    @property
    def flagged(self) -> int:
        """How many questions the user flagged as badly cropped in this session."""
        return self._flagged

    @property
    def pool_exhausted(self) -> bool:
        """Whether a replacement was needed but no question was left to draw."""
        return self._exhausted

    @property
    def finished(self) -> bool:
        """Whether every question has been dealt with."""
        return self._index >= len(self._questions)

    def current(self) -> questionbank.Question | None:
        """The question to show now, or ``None`` when the session is over."""
        if self.finished:
            return None
        return self._questions[self._index]

    def upcoming(self) -> questionbank.Question | None:
        """
        The question after the current one, or ``None`` if this is the last.

        Used by the web layer to warm the browser's image cache for the next
        question while the user is still looking at this one; the desktop
        build has no equivalent need since its images load from local disk.
        """
        next_index = self._index + 1
        if next_index >= len(self._questions):
            return None
        return self._questions[next_index]

    def grade(self, grade: str) -> None:
        """
        Record the user's self-assessment and move to the next question.

        Args:
            grade: One of ``questionbank.api.GRADES``.

        Raises:
            ValueError: If the grade is not a known value, or the session is
                already over.
        """
        if grade not in questionbank.GRADES:
            raise ValueError(f"Unknown grade {grade!r}")

        question = self.current()
        if question is None:
            raise ValueError("The session has no question left to grade")

        repo.record_grade(
            self._conn, self.session.id, question.id, self.position, grade
        )
        self._graded += 1
        self._index += 1
        logger.info(
            "session %d: question %d graded %s (%d of %d)",
            self.session.id,
            question.id,
            grade,
            self._graded,
            self.total,
        )

    def flag_broken(self) -> questionbank.Question | None:
        """
        Flag the current question as badly cropped and replace it.

        No grade is recorded: the question was never really asked. The flag is
        global, so the question will not appear in later sessions either, and it
        drops out of the statistics of any session that already graded it.

        Returns:
            The replacement question now being shown, or ``None`` if no question
            was left to draw - in which case the session is over and
            :attr:`pool_exhausted` is set.

        Raises:
            ValueError: If the session is already over.
        """
        question = self.current()
        if question is None:
            raise ValueError("The session has no question left to flag")

        # Captured before any mutation: current() guarantees index < total, so
        # this is exactly index+1 with no clamping - the position both the
        # replace and the remove path below must act on.
        position = self.position

        questionbank.mark_broken(self._conn, question.id)
        self._conn.commit()
        self._flagged += 1

        try:
            replacement = questionbank.sample_questions(
                self._conn, 1, self._rng, exclude=self._seen
            )[0]
        except ValueError:
            # Nothing left to swap in. Drop the flagged question from the plan so
            # the session ends cleanly instead of showing it again.
            self._questions.pop(self._index)
            self._exhausted = True
            repo.remove_plan_position(self._conn, self.session.id, position)
            logger.warning(
                "session %d: question %d flagged, no replacement left",
                self.session.id,
                question.id,
            )
            return None

        self._questions[self._index] = replacement
        self._seen.add(replacement.id)
        repo.replace_plan_question(self._conn, self.session.id, position, replacement.id)
        logger.info(
            "session %d: question %d flagged as badly cropped, replaced by %d",
            self.session.id,
            question.id,
            replacement.id,
        )
        return replacement

    def finish(self, status: str = STATUS_COMPLETED) -> None:
        """
        Close the session.

        Args:
            status: ``completed`` when the user reached the end, ``abandoned``
                when they left early.
        """
        repo.finish_session(self._conn, self.session.id, status)
        logger.info(
            "session %d %s: %d graded, %d flagged",
            self.session.id,
            status,
            self._graded,
            self._flagged,
        )

    def abandon(self) -> None:
        """Close the session as abandoned."""
        self.finish(STATUS_ABANDONED)


def start_session(
    conn: sqlite3.Connection, size: int, rng: random.Random | None = None
) -> SessionRunner:
    """
    Draw questions and open a session for them.

    Args:
        conn: Open connection.
        size: How many questions to ask.
        rng: Random source, for reproducible tests.

    Returns:
        A runner positioned on the first question.

    Raises:
        NoQuestionsAvailable: If fewer than ``size`` questions can be drawn.
            Questions with no answer in the source and questions flagged as
            badly cropped are not part of the pool.
    """
    try:
        questions = questionbank.sample_questions(conn, size, rng)
    except ValueError as error:
        raise NoQuestionsAvailable(str(error)) from error

    session = repo.create_session(conn, size)
    repo.insert_plan(conn, session.id, [q.id for q in questions])
    logger.info(
        "session %d started: %d questions of %d available",
        session.id,
        size,
        questionbank.count_available(conn),
    )
    return SessionRunner(conn, session, questions, rng)


def get_active_session(conn: sqlite3.Connection) -> ExamSession | None:
    """Return the session still in progress, if the user left one unfinished."""
    return repo.get_active_session(conn)


def resume_session(
    conn: sqlite3.Connection, session_id: int, rng: random.Random | None = None
) -> SessionRunner | None:
    """
    Rebuild a runner for a session that is still in progress.

    Needed because a web request cannot rely on a runner living in process
    memory the way the desktop GUI's one long-lived Tk process could: the user
    may reload the page, switch tabs on a phone, or the pod may restart
    mid-exam. The plan and every stored grade are read back from the database,
    so the resumed runner is on the same question, in the same order, with the
    same questions already graded.

    Args:
        conn: Open connection.
        session_id: Session to resume.
        rng: Random source for any further replacements.

    Returns:
        A runner positioned exactly where the session was left, or ``None`` if
        there is no such session, it is not in progress, or it has no stored
        plan (only possible for a session created before this table existed).
    """
    session = repo.get_session(conn, session_id)
    if session is None or session.status != STATUS_IN_PROGRESS:
        return None

    plan_ids = repo.list_plan(conn, session_id)
    if not plan_ids:
        return None

    loaded = [questionbank.get_question(conn, qid) for qid in plan_ids]
    if any(question is None for question in loaded):
        logger.error(
            "session %d: plan references a question missing from the catalog, cannot resume",
            session_id,
        )
        return None
    questions: list[questionbank.Question] = loaded  # every entry is non-None, checked above

    graded = len(repo.list_answers(conn, session_id))
    return SessionRunner._resume(conn, session, questions, graded, rng)


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def _tally(grades: list[str]) -> Tally:
    """Count a list of grade strings into a :class:`Tally`."""
    return Tally(
        correct=grades.count(questionbank.GRADE_CORRECT),
        partial=grades.count(questionbank.GRADE_PARTIAL),
        incorrect=grades.count(questionbank.GRADE_INCORRECT),
    )


def _available_set(conn: sqlite3.Connection) -> set[int]:
    """
    Question numbers that still count towards the statistics.

    Read through the question bank's public API: this module must not query the
    catalog tables directly. The catalog is small enough that fetching the whole
    set once and filtering in Python is cheaper than joining per query.
    """
    return set(questionbank.available_question_ids(conn))


def list_sessions(conn: sqlite3.Connection) -> list[SessionSummary]:
    """
    Summarise every session, newest first.

    Grades whose question is no longer available are excluded from the tally but
    still counted in :attr:`SessionSummary.recorded`, so the screen can say how
    many answers were set aside.
    """
    available = _available_set(conn)

    by_session: dict[int, list[str]] = {}
    recorded: dict[int, int] = {}
    for row in repo.list_all_answers(conn):
        session_id = row["session_id"]
        recorded[session_id] = recorded.get(session_id, 0) + 1
        if row["question_id"] in available:
            by_session.setdefault(session_id, []).append(row["grade"])

    return [
        SessionSummary(
            session=session,
            tally=_tally(by_session.get(session.id, [])),
            recorded=recorded.get(session.id, 0),
        )
        for session in repo.list_sessions(conn)
    ]


def get_session_detail(
    conn: sqlite3.Connection, session_id: int
) -> SessionDetail | None:
    """
    Return one session with every stored grade, for the review screen.

    Returns:
        The detail, or ``None`` when there is no such session.
    """
    session = repo.get_session(conn, session_id)
    if session is None:
        return None

    available = _available_set(conn)
    answers = tuple(
        AnswerRecord(
            question_id=row["question_id"],
            position=row["position"],
            grade=row["grade"],
            answered_at=row["answered_at"],
            counted=row["question_id"] in available,
        )
        for row in repo.list_answers(conn, session_id)
    )

    summary = SessionSummary(
        session=session,
        tally=_tally([a.grade for a in answers if a.counted]),
        recorded=len(answers),
    )
    return SessionDetail(summary=summary, answers=answers)


def get_overview(conn: sqlite3.Connection) -> Overview:
    """
    Aggregate every session into one set of figures.

    Sessions are judged individually against the pass threshold, so
    ``passed_sessions`` is the number of sessions that cleared it, not a
    percentage of answers.
    """
    summaries = list_sessions(conn)
    grades: list[str] = []

    available = _available_set(conn)
    for row in repo.list_all_answers(conn):
        if row["question_id"] in available:
            grades.append(row["grade"])

    return Overview(
        tally=_tally(grades),
        sessions=len(summaries),
        completed_sessions=sum(
            1 for s in summaries if s.session.status == STATUS_COMPLETED
        ),
        passed_sessions=sum(1 for s in summaries if s.tally.passed),
    )


def reset_session(conn: sqlite3.Connection, session_id: int) -> bool:
    """
    Delete one session's statistics.

    Returns:
        ``True`` if a session was removed.
    """
    removed = repo.delete_session(conn, session_id)
    if removed:
        logger.info("session %d reset", session_id)
    return removed


def reset_all_sessions(conn: sqlite3.Connection) -> int:
    """
    Delete the statistics of every session.

    The question catalog and the badly-cropped flags survive; only the user's
    answer history is removed.

    Returns:
        Number of sessions removed.
    """
    removed = repo.delete_all_sessions(conn)
    logger.info("all statistics reset: %d sessions removed", removed)
    return removed


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def get_default_question_count(conn: sqlite3.Connection) -> int:
    """How many questions the new-session screen offers by default."""
    raw = repo.get_setting(conn, SETTING_DEFAULT_COUNT)
    if raw is None:
        return DEFAULT_QUESTION_COUNT
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("ignoring malformed %s setting: %r", SETTING_DEFAULT_COUNT, raw)
        return DEFAULT_QUESTION_COUNT


def set_default_question_count(conn: sqlite3.Connection, count: int) -> None:
    """Remember the preferred session size."""
    repo.set_setting(conn, SETTING_DEFAULT_COUNT, str(max(1, count)))


def get_auto_reveal(conn: sqlite3.Connection) -> bool:
    """
    Whether the answer appears without pressing "show answer".

    Off by default: the point of the exercise is to answer first.
    """
    raw = repo.get_setting(conn, SETTING_AUTO_REVEAL)
    if raw is None:
        return DEFAULT_AUTO_REVEAL
    return raw == "1"


def set_auto_reveal(conn: sqlite3.Connection, enabled: bool) -> None:
    """Remember whether to reveal the answer immediately."""
    repo.set_setting(conn, SETTING_AUTO_REVEAL, "1" if enabled else "0")


# code-documentation-2026-09-06T09:40:00
