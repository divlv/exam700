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
    ENTRY_GRADED,
    ENTRY_MARKED,
    ENTRY_PENDING,
    FULL_CREDIT,
    PARTIAL_CREDIT_PERCENT,
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
from api.modules.questionbank import api as questionbank
from api.modules.shared.api import get_logger

logger = get_logger(__name__)

#: Setting keys. Defaults apply until the user changes them on the settings screen.
SETTING_DEFAULT_COUNT = "default_question_count"
SETTING_AUTO_REVEAL = "auto_reveal_answer"

DEFAULT_QUESTION_COUNT = 20
DEFAULT_AUTO_REVEAL = False

#: How many random questions compete each time the next one is chosen.
SELECTION_WINDOW = 10


class NoQuestionsAvailable(RuntimeError):
    """Raised when a session cannot be started because the pool is too small."""


def _credit_for(grade: str) -> int:
    """
    Points a grade earns under the scoring rule in force now, in hundredths.

    Called once, when the answer is graded, and the result is stored on the
    answer. Statistics read the stored value and never call this, which is what
    keeps a later change to the rule from re-scoring past sessions.
    """
    if grade == questionbank.GRADE_CORRECT:
        return FULL_CREDIT
    if grade == questionbank.GRADE_PARTIAL:
        return PARTIAL_CREDIT_PERCENT
    return 0


def _choose_next(
    conn: sqlite3.Connection, exclude: set[int], rng: random.Random | None
) -> questionbank.Question | None:
    """
    Pick the next question to show, steering towards what needs practice.

    A random window of :data:`SELECTION_WINDOW` available questions competes.
    Any question in it that was never answered wins, picked at random among
    those; if every one has been answered, the one with the most incorrect
    answers wins, ties again broken at random. Correct and partial answers are
    deliberately not told apart.

    Args:
        conn: Open connection.
        exclude: Questions that must not be chosen, normally the ones this
            session has already drawn.
        rng: Random source, for reproducible tests.

    Returns:
        The chosen question, or ``None`` when no available question is left
        outside ``exclude``.
    """
    source = rng or random
    candidates = [
        qid for qid in questionbank.available_question_ids(conn) if qid not in exclude
    ]
    if not candidates:
        return None

    window = source.sample(candidates, min(SELECTION_WINDOW, len(candidates)))
    history = repo.answer_history(conn, window)

    contenders = [qid for qid in window if history[qid].attempts == 0]
    if not contenders:
        worst = max(history[qid].incorrect for qid in window)
        contenders = [qid for qid in window if history[qid].incorrect == worst]

    return questionbank.get_question(conn, source.choice(contenders))


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
        marked: set[int] | None = None,
        grades: dict[int, str] | None = None,
    ):
        """
        Args:
            conn: Open connection, used for every write the session makes.
            session: The session row.
            questions: Questions drawn for it, in plan order.
            rng: Random source for drawing replacements.
            marked: Numbers of questions already postponed with the mark.
            grades: Grades already given, by question number.
        """
        self._conn = conn
        self._rng = rng
        self.session = session
        self._questions = list(questions)
        # Progress is kept per question, not as a cursor: with postponed
        # questions and free navigation, the graded ones are not necessarily
        # the first N of the plan.
        self._marked: set[int] = set(marked or ())
        self._grades: dict[int, str] = dict(grades or {})
        # Every question this session has drawn, so a replacement can never be
        # one the user has already seen here.
        self._seen: set[int] = {question.id for question in self._questions}
        self._flagged = 0
        self._exhausted = False

    @classmethod
    def _resume(
        cls,
        conn: sqlite3.Connection,
        session: ExamSession,
        questions: list[questionbank.Question],
        marked: set[int],
        grades: dict[int, str],
        rng: random.Random | None,
    ) -> "SessionRunner":
        """
        Reconstruct a runner from its persisted plan, marks and stored grades.

        Args:
            conn: Open connection.
            session: The in-progress session being resumed.
            questions: The full drawn plan, in position order, as currently
                stored (already reflecting any flag/replace done before the
                process that ran this session last exited).
            marked: Numbers of the plan's questions carrying the mark.
            grades: Stored grades by question number. Grades of questions that
                have since left the plan are ignored.
            rng: Random source for drawing further replacements.

        Note:
            The desktop build's ``flagged`` counter is not persisted - only the
            global broken-question flag is - so it restarts at 0 on resume.
            This affects only the in-session "flagged: N" display, never the
            availability rules or the statistics.
        """
        in_plan = {question.id for question in questions}
        runner = cls(
            conn,
            session,
            questions,
            rng,
            marked={qid for qid in marked if qid in in_plan},
            grades={qid: grade for qid, grade in grades.items() if qid in in_plan},
        )
        # The stored plan already reflects every replacement made so far, so it
        # doubles as the "ids ever drawn" set; questions that were flagged and
        # swapped out are excluded from future draws globally regardless.
        runner._seen = in_plan
        return runner

    def _is_pending(self, question: questionbank.Question) -> bool:
        """Whether the question is neither graded nor postponed."""
        return question.id not in self._grades and question.id not in self._marked

    def _pending(self) -> list[questionbank.Question]:
        """Questions still to be shown in the normal pass, in plan order."""
        return [question for question in self._questions if self._is_pending(question)]

    def _locate(self, position: int | None) -> tuple[int, questionbank.Question]:
        """
        Resolve a one-based plan position to ``(index, question)``.

        ``None`` means the question the pass is on now.

        Raises:
            ValueError: If there is no such question.
        """
        if position is None:
            question = self.current()
            if question is None:
                raise ValueError("The session has no question left")
            return self._questions.index(question), question
        if not 1 <= position <= len(self._questions):
            raise ValueError(f"No question at position {position}")
        return position - 1, self._questions[position - 1]

    @property
    def total(self) -> int:
        """How many questions the session will ask - its requested length."""
        return len(self._questions)

    @property
    def position(self) -> int:
        """One-based plan position of the question being shown."""
        pending = self._pending()
        if not pending:
            return self.total
        return self._questions.index(pending[0]) + 1

    @property
    def graded(self) -> int:
        """How many of the session's questions have a grade."""
        return sum(1 for question in self._questions if question.id in self._grades)

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
        """
        Whether the normal pass is over: nothing is left that is neither
        graded nor postponed. Postponed questions may still be waiting.
        """
        return not self._pending()

    @property
    def has_marked(self) -> bool:
        """Whether any question is postponed and waiting to be come back to."""
        return any(question.id in self._marked for question in self._questions)

    @property
    def marked_positions(self) -> list[int]:
        """Plan positions of the postponed questions, in order."""
        return [
            index + 1
            for index, question in enumerate(self._questions)
            if question.id in self._marked
        ]

    @property
    def unresolved(self) -> int:
        """How many questions have no grade yet, postponed or not."""
        return self.total - self.graded

    @property
    def complete(self) -> bool:
        """Whether every question is graded, with nothing postponed."""
        return self.finished and not self.has_marked

    def current(self) -> questionbank.Question | None:
        """
        The question to show now: the first one that is neither graded nor
        postponed, or ``None`` when the normal pass is over.
        """
        pending = self._pending()
        return pending[0] if pending else None

    def upcoming(self) -> questionbank.Question | None:
        """
        The question after the current one in the queue, or ``None``.

        Used by the web layer to warm the browser's image cache for the next
        question while the user is still looking at this one; the desktop
        build has no equivalent need since its images load from local disk.
        """
        pending = self._pending()
        return pending[1] if len(pending) > 1 else None

    def question_at(self, position: int) -> questionbank.Question | None:
        """The question at a one-based plan position, or ``None`` if out of range."""
        if not 1 <= position <= len(self._questions):
            return None
        return self._questions[position - 1]

    def entry_at(self, position: int) -> PlanEntry | None:
        """How the question at a plan position stands, or ``None`` if out of range."""
        question = self.question_at(position)
        if question is None:
            return None
        return self._entry(position, question)

    def entries(self) -> list[PlanEntry]:
        """Every question of the session with its state, for the review screen."""
        return [
            self._entry(index + 1, question)
            for index, question in enumerate(self._questions)
        ]

    def _entry(self, position: int, question: questionbank.Question) -> PlanEntry:
        if question.id in self._grades:
            return PlanEntry(position, question.id, ENTRY_GRADED, self._grades[question.id])
        if question.id in self._marked:
            return PlanEntry(position, question.id, ENTRY_MARKED)
        return PlanEntry(position, question.id, ENTRY_PENDING)

    def grade(self, grade: str, position: int | None = None) -> None:
        """
        Record the user's self-assessment of a question.

        Without a position this grades the question the pass is on. With one it
        grades that question, graded already or not: a new grade replaces the
        old one, which is how a mis-pressed button is corrected from the review
        screen. Grading a postponed question clears its mark.

        Args:
            grade: One of ``questionbank.api.GRADES``.
            position: One-based plan position, or ``None`` for the current one.

        Raises:
            ValueError: If the grade is not a known value, or there is no such
                question.
        """
        if grade not in questionbank.GRADES:
            raise ValueError(f"Unknown grade {grade!r}")

        index, question = self._locate(position)

        repo.record_grade(
            self._conn,
            self.session.id,
            question.id,
            index + 1,
            grade,
            _credit_for(grade),
        )
        self._grades[question.id] = grade
        if question.id in self._marked:
            self._marked.discard(question.id)
            repo.set_marked(self._conn, self.session.id, index + 1, False)
        logger.info(
            "session %d: question %d graded %s (%d of %d)",
            self.session.id,
            question.id,
            grade,
            self.graded,
            self.total,
        )

    def mark(self, position: int | None = None) -> None:
        """
        Postpone a question without grading it, to come back to it later.

        Its answer is not counted for now. The mark is cleared when the question
        is graded.

        Raises:
            ValueError: If there is no such question, or it already has a grade -
                the mark means "no answer given yet".
        """
        index, question = self._locate(position)
        if question.id in self._grades:
            raise ValueError(f"Question {question.id} is already graded")
        if question.id in self._marked:
            return
        self._marked.add(question.id)
        repo.set_marked(self._conn, self.session.id, index + 1, True)
        logger.info("session %d: question %d postponed", self.session.id, question.id)

    def unmark(self, position: int) -> None:
        """Take the mark off a postponed question; it waits for its turn again."""
        index, question = self._locate(position)
        if question.id not in self._marked:
            return
        self._marked.discard(question.id)
        repo.set_marked(self._conn, self.session.id, index + 1, False)

    def flag_broken(self, position: int | None = None) -> questionbank.Question | None:
        """
        Flag a question as badly cropped and replace it.

        No grade is recorded for the replacement: it was never really asked. The
        flag is global, so the question will not appear in later sessions either,
        and it drops out of the statistics of any session that already graded it.

        Args:
            position: One-based plan position, or ``None`` for the current one.

        Returns:
            The replacement question now at that position, or ``None`` if no
            question was left to draw - in which case the position leaves the
            plan and :attr:`pool_exhausted` is set.

        Raises:
            ValueError: If there is no such question.
        """
        index, question = self._locate(position)

        # Captured before any mutation: it is the position both the replace and
        # the remove path below must act on.
        plan_position = index + 1

        questionbank.mark_broken(self._conn, question.id)
        self._conn.commit()
        self._flagged += 1
        self._marked.discard(question.id)
        self._grades.pop(question.id, None)

        replacement = _choose_next(self._conn, self._seen, self._rng)
        if replacement is None:
            # Nothing left to swap in. Drop the flagged question from the plan so
            # the session ends cleanly instead of showing it again.
            self._questions.pop(index)
            self._exhausted = True
            repo.remove_plan_position(self._conn, self.session.id, plan_position)
            logger.warning(
                "session %d: question %d flagged, no replacement left",
                self.session.id,
                question.id,
            )
            return None

        self._questions[index] = replacement
        self._seen.add(replacement.id)
        repo.replace_plan_question(self._conn, self.session.id, plan_position, replacement.id)
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

        Finishing the test counts every question still without a grade -
        postponed ones and ones never shown - as incorrect, as an exam does with
        questions left unanswered. They are written as ordinary incorrect
        answers, so the statistics and the answer history see them like any
        other. Abandoning the session is different: the user is leaving, and
        only the grades actually given are kept.

        Args:
            status: ``completed`` when the user finished the test, ``abandoned``
                when they left early.
        """
        if status == STATUS_COMPLETED:
            self._grade_unresolved_as_incorrect()
        repo.finish_session(self._conn, self.session.id, status)
        logger.info(
            "session %d %s: %d graded, %d flagged",
            self.session.id,
            status,
            self.graded,
            self._flagged,
        )

    def _grade_unresolved_as_incorrect(self) -> None:
        """Give every question that has no grade yet an incorrect one."""
        skipped = 0
        for index, question in enumerate(self._questions):
            if question.id in self._grades:
                continue
            repo.record_grade(
                self._conn,
                self.session.id,
                question.id,
                index + 1,
                questionbank.GRADE_INCORRECT,
                _credit_for(questionbank.GRADE_INCORRECT),
            )
            self._grades[question.id] = questionbank.GRADE_INCORRECT
            if question.id in self._marked:
                self._marked.discard(question.id)
                repo.set_marked(self._conn, self.session.id, index + 1, False)
            skipped += 1
        if skipped:
            logger.info(
                "session %d: %d unanswered question(s) counted as incorrect",
                self.session.id,
                skipped,
            )

    def abandon(self) -> None:
        """Close the session as abandoned."""
        self.finish(STATUS_ABANDONED)


def start_session(
    conn: sqlite3.Connection, size: int, rng: random.Random | None = None
) -> SessionRunner:
    """
    Draw questions and open a session for them.

    The questions are chosen one at a time by :func:`_choose_next`, so the user's
    history of answers steers the plan towards unanswered and badly answered
    questions. Choosing them all up front is equivalent to choosing as the
    session goes: a question appears once per session, so nothing answered
    during the session can change the history of a question still to come.

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
    if size < 1:
        raise NoQuestionsAvailable("Session size must be at least 1")
    available = questionbank.count_available(conn)
    if size > available:
        raise NoQuestionsAvailable(
            f"Requested {size} questions but only {available} are available"
        )

    questions: list[questionbank.Question] = []
    chosen: set[int] = set()
    for _ in range(size):
        question = _choose_next(conn, chosen, rng)
        if question is None:  # cannot happen: the pool was checked above
            raise NoQuestionsAvailable("The question pool ran out while drawing")
        questions.append(question)
        chosen.add(question.id)

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

    plan = repo.list_plan_entries(conn, session_id)
    if not plan:
        return None
    plan_ids = [question_id for _, question_id, _ in plan]
    marked = {question_id for _, question_id, is_marked in plan if is_marked}

    loaded = [questionbank.get_question(conn, qid) for qid in plan_ids]
    if any(question is None for question in loaded):
        logger.error(
            "session %d: plan references a question missing from the catalog, cannot resume",
            session_id,
        )
        return None
    questions: list[questionbank.Question] = loaded  # every entry is non-None, checked above

    # Grades are matched to plan questions by number, not counted: with
    # postponed questions the graded ones are not necessarily the first N.
    grades = {row["question_id"]: row["grade"] for row in repo.list_answers(conn, session_id)}
    return SessionRunner._resume(conn, session, questions, marked, grades, rng)


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def _tally(answers: list[tuple[str, int]]) -> Tally:
    """
    Count ``(grade, credit)`` pairs into a :class:`Tally`.

    The credit is the value stored with each answer, not recomputed from the
    grade, so sessions graded under an older scoring rule keep their figures.
    """
    grades = [grade for grade, _ in answers]
    return Tally(
        correct=grades.count(questionbank.GRADE_CORRECT),
        partial=grades.count(questionbank.GRADE_PARTIAL),
        incorrect=grades.count(questionbank.GRADE_INCORRECT),
        credit=sum(credit for _, credit in answers),
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

    by_session: dict[int, list[tuple[str, int]]] = {}
    recorded: dict[int, int] = {}
    for row in repo.list_all_answers(conn):
        session_id = row["session_id"]
        recorded[session_id] = recorded.get(session_id, 0) + 1
        if row["question_id"] in available:
            by_session.setdefault(session_id, []).append((row["grade"], row["credit"]))

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
            credit=row["credit"],
        )
        for row in repo.list_answers(conn, session_id)
    )

    summary = SessionSummary(
        session=session,
        tally=_tally([(a.grade, a.credit) for a in answers if a.counted]),
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
    answers: list[tuple[str, int]] = []

    available = _available_set(conn)
    for row in repo.list_all_answers(conn):
        if row["question_id"] in available:
            answers.append((row["grade"], row["credit"]))

    return Overview(
        tally=_tally(answers),
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
