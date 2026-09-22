"""
Domain model of exam sessions and their statistics.

A session draws a fixed number of questions, shows each one, and records the
grade the user gives themselves. Two rules shape everything here:

* **Only available questions count.** A question the user later flags as badly
  cropped, or one the source PDF never answered, drops out of every figure
  retroactively. The stored grades are never deleted, so removing a flag brings
  them back.
* **The session keeps its requested length.** Flagging a question during a
  session does not shorten it: a replacement is drawn immediately, and the
  flagged question is recorded nowhere.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Percentage of correct answers needed to pass the real exam.
PASS_THRESHOLD_PERCENT = 70.0

#: A session is running, was played to the end, or was abandoned part way.
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_ABANDONED = "abandoned"


@dataclass(frozen=True)
class ExamSession:
    """
    One exam session as stored.

    Attributes:
        id: Database identifier, shown to the user as the session number.
        started_at: ISO-8601 timestamp of when the session began.
        finished_at: ISO-8601 timestamp of when it ended, or ``None`` while it
            is still running.
        planned_count: How many questions the user asked for.
        status: One of :data:`STATUS_IN_PROGRESS`, :data:`STATUS_COMPLETED`,
            :data:`STATUS_ABANDONED`.
    """

    id: int
    started_at: str
    finished_at: str | None
    planned_count: int
    status: str


@dataclass(frozen=True)
class AnswerRecord:
    """
    One graded question inside a session.

    Attributes:
        question_id: Question number that was shown.
        position: One-based place in the session's order.
        grade: ``correct``, ``partial`` or ``incorrect``.
        answered_at: ISO-8601 timestamp of when the grade was given.
        counted: Whether the question is still available and therefore still
            contributes to the statistics. ``False`` once it is flagged as badly
            cropped or found to have no answer in the source.
    """

    question_id: int
    position: int
    grade: str
    answered_at: str
    counted: bool


@dataclass(frozen=True)
class Tally:
    """
    Counts of grades, with the derived percentages.

    Only counted answers reach a tally, so a flagged question never shows up in
    one.

    Attributes:
        correct: Answers graded correct.
        partial: Answers graded partially correct.
        incorrect: Answers graded incorrect.
    """

    correct: int = 0
    partial: int = 0
    incorrect: int = 0

    @property
    def total(self) -> int:
        """How many answers were counted."""
        return self.correct + self.partial + self.incorrect

    @property
    def percent_correct(self) -> float:
        """Share of correct answers, or 0.0 when nothing was counted."""
        return 100.0 * self.correct / self.total if self.total else 0.0

    @property
    def percent_partial(self) -> float:
        """Share of partially correct answers."""
        return 100.0 * self.partial / self.total if self.total else 0.0

    @property
    def percent_incorrect(self) -> float:
        """Share of incorrect answers."""
        return 100.0 * self.incorrect / self.total if self.total else 0.0

    @property
    def passed(self) -> bool:
        """
        Whether the result clears the exam threshold.

        An empty tally never passes: with nothing counted there is no result to
        judge, which the screens show as "no counted questions".
        """
        return self.total > 0 and self.percent_correct >= PASS_THRESHOLD_PERCENT


@dataclass(frozen=True)
class SessionSummary:
    """
    A session with its tally, for the results list.

    Attributes:
        session: The stored session.
        tally: Grades that still count.
        recorded: How many grades are stored, including ones no longer counted.
    """

    session: ExamSession
    tally: Tally
    recorded: int

    @property
    def excluded(self) -> int:
        """Stored grades that no longer count, because the question was flagged."""
        return self.recorded - self.tally.total


@dataclass(frozen=True)
class SessionDetail:
    """
    Everything one session's review screen needs.

    Attributes:
        summary: The session and its tally.
        answers: Every stored grade in session order, counted or not.
    """

    summary: SessionSummary
    answers: tuple[AnswerRecord, ...]

    @property
    def mistakes(self) -> tuple[AnswerRecord, ...]:
        """
        Counted answers that were not fully correct.

        This is the mistake-analysis list, so answers that no longer count are
        left out of it.
        """
        return tuple(
            answer
            for answer in self.answers
            if answer.counted and answer.grade != "correct"
        )


@dataclass(frozen=True)
class Overview:
    """
    Statistics across every session.

    Attributes:
        tally: All counted grades from all sessions.
        sessions: How many sessions are stored.
        completed_sessions: How many were played to the end.
        passed_sessions: How many cleared the threshold on their own.
    """

    tally: Tally
    sessions: int
    completed_sessions: int
    passed_sessions: int


# code-documentation-2026-09-06T09:40:00
