"""
Tests for running a session and for what the statistics count.

The rules worth pinning down are the ones a user would notice if they broke:
a session keeps its requested length when a question is flagged, and flagging a
question removes it from results that were already recorded.
"""

from __future__ import annotations

import random
from dataclasses import replace

import pytest

from api.modules.examsession import api as examsession
from api.modules.questionbank import api as questionbank
from api.modules.shared.api import get_connection


@pytest.fixture()
def connection(tmp_path):
    """A throw-away database with a small catalog of usable questions."""
    conn = get_connection(tmp_path / "test.sqlite")
    questionbank.ensure_schema(conn)
    examsession.ensure_schema(conn)
    for number in range(1, 21):
        questionbank.upsert_question(conn, make_question(number))
    conn.commit()
    yield conn
    conn.close()


def make_question(number: int) -> questionbank.Question:
    """Build a catalog row for the given question number."""
    return questionbank.Question(
        id=number,
        source_file="az-700_exam_questions_with_answers_01.pdf",
        start_page=0,
        end_page=1,
        question_image=f"images/q{number:04d}_question.png",
        answer_image=f"images/q{number:04d}_answer.png",
        geometry={"dpi": 200},
    )


def play(runner, grades):
    """Grade a session with the given sequence of grades."""
    for grade in grades:
        runner.grade(grade)
    runner.finish()


def test_schema_migration_is_idempotent(connection):
    """Start-up runs the migration every time."""
    version = examsession.ensure_schema(connection)
    assert examsession.ensure_schema(connection) == version


def test_a_session_asks_the_requested_number_of_questions(connection):
    """The runner walks exactly through the drawn questions."""
    runner = examsession.start_session(connection, 5, random.Random(1))

    assert runner.total == 5
    assert runner.position == 1

    seen = []
    while not runner.finished:
        seen.append(runner.current().id)
        runner.grade(examsession.GRADE_CORRECT)

    assert len(seen) == 5
    assert len(set(seen)) == 5, "a session must not repeat a question"
    assert runner.graded == 5
    assert runner.current() is None


def test_starting_a_session_is_refused_when_the_pool_is_too_small(connection):
    """Asking for more than is available is a clear error, not a short session."""
    with pytest.raises(examsession.NoQuestionsAvailable):
        examsession.start_session(connection, 21)


def test_flagging_a_question_keeps_the_session_length(connection):
    """
    The user's choice: a flagged question is replaced, not dropped.

    The counter must stay "question k of n" and the session must still end with
    the requested number of grades.
    """
    runner = examsession.start_session(connection, 5, random.Random(2))
    flagged_id = runner.current().id

    replacement = runner.flag_broken()

    assert replacement is not None
    assert replacement.id != flagged_id
    assert runner.total == 5
    assert runner.position == 1, "the counter must not advance on a flag"
    assert runner.current().id == replacement.id

    while not runner.finished:
        runner.grade(examsession.GRADE_CORRECT)
    runner.finish()

    detail = examsession.get_session_detail(connection, runner.session.id)
    assert detail.summary.tally.total == 5
    assert flagged_id not in {answer.question_id for answer in detail.answers}


def test_a_flagged_question_is_recorded_nowhere_but_the_flag(connection):
    """Flagging writes no grade: the question was never really asked."""
    runner = examsession.start_session(connection, 3, random.Random(3))
    flagged_id = runner.current().id

    runner.flag_broken()

    assert questionbank.is_available(connection, flagged_id) is False
    assert [mark.question.id for mark in questionbank.list_broken(connection)] == [
        flagged_id
    ]
    detail = examsession.get_session_detail(connection, runner.session.id)
    assert detail.answers == ()


def test_replacement_is_never_a_question_already_seen(connection):
    """Flagging repeatedly must not hand back an earlier question."""
    runner = examsession.start_session(connection, 4, random.Random(4))

    seen = set()
    for _ in range(6):
        question = runner.current()
        assert question.id not in seen
        seen.add(question.id)
        assert runner.flag_broken() is not None

    assert runner.total == 4


def test_the_session_ends_cleanly_when_no_replacement_is_left(connection):
    """
    With the pool exhausted the session must stop, not loop on a flagged item.

    Twenty questions exist; a session of twenty leaves nothing to swap in.
    """
    runner = examsession.start_session(connection, 20, random.Random(5))

    assert runner.flag_broken() is None
    assert runner.pool_exhausted is True
    assert runner.total == 19, "the flagged question leaves the plan"

    while not runner.finished:
        runner.grade(examsession.GRADE_CORRECT)
    runner.finish()

    assert examsession.get_session_detail(connection, runner.session.id).summary.tally.total == 19


def test_grades_are_stored_as_they_are_given(connection):
    """An interrupted session keeps whatever was already answered."""
    runner = examsession.start_session(connection, 5, random.Random(6))
    runner.grade(examsession.GRADE_CORRECT)
    runner.grade(examsession.GRADE_INCORRECT)
    runner.abandon()

    summaries = examsession.list_sessions(connection)
    assert len(summaries) == 1
    assert summaries[0].session.status == examsession.STATUS_ABANDONED
    assert summaries[0].tally.total == 2
    assert summaries[0].tally.correct == 1
    assert summaries[0].tally.incorrect == 1


def test_an_unknown_grade_is_rejected(connection):
    """Guards the vocabulary shared with the question bank."""
    runner = examsession.start_session(connection, 2, random.Random(7))

    with pytest.raises(ValueError):
        runner.grade("almost")


def test_tally_percentages_and_pass_threshold(connection):
    """Seven correct out of ten is a pass; six is not."""
    runner = examsession.start_session(connection, 10, random.Random(8))
    play(
        runner,
        [examsession.GRADE_CORRECT] * 7
        + [examsession.GRADE_PARTIAL] * 2
        + [examsession.GRADE_INCORRECT],
    )

    summary = examsession.list_sessions(connection)[0]
    assert summary.tally.total == 10
    assert summary.tally.percent_correct == 70.0
    assert summary.tally.passed is True

    runner = examsession.start_session(connection, 10, random.Random(9))
    play(runner, [examsession.GRADE_CORRECT] * 6 + [examsession.GRADE_INCORRECT] * 4)

    newest = examsession.list_sessions(connection)[0]
    assert newest.tally.percent_correct == 60.0
    assert newest.tally.passed is False


def test_flagging_removes_a_question_from_results_already_recorded(connection):
    """
    The user's choice: exclusion applies retroactively.

    A question graded in an earlier session stops counting the moment it is
    flagged, and starts counting again when the flag is removed.
    """
    runner = examsession.start_session(connection, 10, random.Random(10))
    graded = []
    while not runner.finished:
        graded.append(runner.current().id)
        runner.grade(
            examsession.GRADE_CORRECT if len(graded) <= 7 else examsession.GRADE_INCORRECT
        )
    runner.finish()

    before = examsession.list_sessions(connection)[0]
    assert (before.tally.total, before.tally.correct) == (10, 7)

    # Flag one of the three the user got wrong: the score should improve.
    questionbank.mark_broken(connection, graded[-1])
    connection.commit()

    after = examsession.list_sessions(connection)[0]
    assert after.tally.total == 9
    assert after.tally.correct == 7
    assert after.recorded == 10, "the grade itself is kept"
    assert after.excluded == 1

    questionbank.unmark_broken(connection, graded[-1])
    connection.commit()

    restored = examsession.list_sessions(connection)[0]
    assert (restored.tally.total, restored.tally.correct) == (10, 7)


def test_questions_without_an_answer_never_count(connection):
    """
    A question the source never answered drops out of the figures too.

    It cannot be drawn any more, but a session recorded before the catalog knew
    that would still hold its grade.
    """
    runner = examsession.start_session(connection, 5, random.Random(11))
    graded = []
    while not runner.finished:
        graded.append(runner.current().id)
        runner.grade(examsession.GRADE_CORRECT)
    runner.finish()

    questionbank.upsert_question(
        connection, replace(make_question(graded[0]), has_answer=False)
    )
    connection.commit()

    summary = examsession.list_sessions(connection)[0]
    assert summary.tally.total == 4
    assert summary.recorded == 5


def test_a_session_with_nothing_left_to_count(connection):
    """Shown to the user as "no counted questions" rather than as 0%."""
    runner = examsession.start_session(connection, 3, random.Random(12))
    graded = []
    while not runner.finished:
        graded.append(runner.current().id)
        runner.grade(examsession.GRADE_CORRECT)
    runner.finish()

    for question_id in graded:
        questionbank.mark_broken(connection, question_id)
    connection.commit()

    summary = examsession.list_sessions(connection)[0]
    assert summary.tally.total == 0
    assert summary.tally.percent_correct == 0.0
    assert summary.tally.passed is False, "an empty tally must never pass"
    assert summary.excluded == 3


def test_mistake_list_skips_answers_that_no_longer_count(connection):
    """The review screen must not offer a flagged question for analysis."""
    runner = examsession.start_session(connection, 4, random.Random(13))
    graded = []
    while not runner.finished:
        graded.append(runner.current().id)
        runner.grade(examsession.GRADE_INCORRECT)
    runner.finish()

    detail = examsession.get_session_detail(connection, runner.session.id)
    assert len(detail.mistakes) == 4

    questionbank.mark_broken(connection, graded[0])
    connection.commit()

    detail = examsession.get_session_detail(connection, runner.session.id)
    assert len(detail.mistakes) == 3
    assert graded[0] not in {answer.question_id for answer in detail.mistakes}


def test_overview_aggregates_every_session(connection):
    """The overall figures and how many sessions passed on their own."""
    runner = examsession.start_session(connection, 10, random.Random(14))
    play(runner, [examsession.GRADE_CORRECT] * 8 + [examsession.GRADE_INCORRECT] * 2)

    runner = examsession.start_session(connection, 10, random.Random(15))
    play(runner, [examsession.GRADE_CORRECT] * 5 + [examsession.GRADE_INCORRECT] * 5)

    overview = examsession.get_overview(connection)
    assert overview.sessions == 2
    assert overview.completed_sessions == 2
    assert overview.tally.total == 20
    assert overview.tally.correct == 13
    assert overview.passed_sessions == 1


def test_reset_one_session_leaves_the_others(connection):
    """Resetting removes the session and its grades, nothing else."""
    first = examsession.start_session(connection, 3, random.Random(16))
    play(first, [examsession.GRADE_CORRECT] * 3)
    second = examsession.start_session(connection, 3, random.Random(17))
    play(second, [examsession.GRADE_PARTIAL] * 3)

    assert examsession.reset_session(connection, first.session.id) is True
    assert examsession.reset_session(connection, first.session.id) is False

    remaining = examsession.list_sessions(connection)
    assert [s.session.id for s in remaining] == [second.session.id]
    assert examsession.get_session_detail(connection, first.session.id) is None


def test_reset_all_sessions_keeps_the_catalog_and_the_flags(connection):
    """A statistics reset must not undo the user's badly-cropped flags."""
    runner = examsession.start_session(connection, 3, random.Random(18))
    flagged_id = runner.current().id
    runner.flag_broken()
    play(runner, [examsession.GRADE_CORRECT] * 3)

    assert examsession.reset_all_sessions(connection) == 1
    assert examsession.list_sessions(connection) == []
    assert examsession.get_overview(connection).tally.total == 0
    assert questionbank.count(connection) == 20
    assert questionbank.is_available(connection, flagged_id) is False


def test_schema_is_at_the_latest_version(connection):
    """v2 added the session plan table, v3 the per-answer credit."""
    assert examsession.ensure_schema(connection) == 3


def test_get_active_session_tracks_the_running_session(connection):
    """The web menu screen offers a resume banner from this, not from memory."""
    assert examsession.get_active_session(connection) is None

    runner = examsession.start_session(connection, 3, random.Random(20))
    active = examsession.get_active_session(connection)
    assert active is not None
    assert active.id == runner.session.id

    runner.finish()
    assert examsession.get_active_session(connection) is None


def test_resume_session_restores_position_and_remaining_questions(connection):
    """
    A resumed runner must behave exactly like the original would have.

    This is the scenario a phone browser forces: the process that started the
    session is gone (page reload, tab discard, pod restart), and a brand new
    runner has to pick up on the same question with the same history.
    """
    runner = examsession.start_session(connection, 5, random.Random(21))
    runner.grade(examsession.GRADE_CORRECT)
    runner.grade(examsession.GRADE_INCORRECT)
    expected_current = runner.current().id

    resumed = examsession.resume_session(connection, runner.session.id)

    assert resumed is not None
    assert resumed.total == 5
    assert resumed.graded == 2
    assert resumed.position == 3
    assert resumed.current().id == expected_current

    while not resumed.finished:
        resumed.grade(examsession.GRADE_CORRECT)
    resumed.finish()

    detail = examsession.get_session_detail(connection, runner.session.id)
    assert detail.summary.tally.total == 5
    assert detail.summary.tally.correct == 4
    assert detail.summary.tally.incorrect == 1


def test_resume_session_keeps_a_replacement_made_before_the_restart(connection):
    """A flag-and-replace done before resuming must not be lost or reverted."""
    runner = examsession.start_session(connection, 4, random.Random(22))
    flagged_id = runner.current().id
    replacement = runner.flag_broken()

    resumed = examsession.resume_session(connection, runner.session.id)

    assert resumed.current().id == replacement.id
    assert resumed.total == 4
    assert resumed.position == 1

    # The original in-memory runner and the resumed one must not disagree about
    # what counts as "already seen" for further replacements.
    assert resumed.flag_broken() is not None
    assert flagged_id not in {
        resumed.current().id,
    }


def test_resume_session_keeps_the_shorter_plan_after_pool_exhaustion(connection):
    """A pop-on-exhaustion done before restarting must survive resume."""
    runner = examsession.start_session(connection, 20, random.Random(23))
    assert runner.flag_broken() is None
    assert runner.total == 19

    resumed = examsession.resume_session(connection, runner.session.id)

    assert resumed is not None
    assert resumed.total == 19
    assert resumed.graded == 0


def test_resume_session_returns_none_once_finished(connection):
    """A completed or abandoned session is not something to resume any more."""
    runner = examsession.start_session(connection, 2, random.Random(24))
    play(runner, [examsession.GRADE_CORRECT] * 2)

    assert examsession.resume_session(connection, runner.session.id) is None


def test_resume_session_returns_none_for_an_unknown_session(connection):
    """Guards the web layer against a stale or forged session id in a URL."""
    assert examsession.resume_session(connection, 999999) is None


def test_settings_round_trip_with_defaults(connection):
    """Settings fall back to sensible values until the user changes them."""
    assert examsession.get_default_question_count(connection) == 20
    assert examsession.get_auto_reveal(connection) is False

    examsession.set_default_question_count(connection, 30)
    examsession.set_auto_reveal(connection, True)

    assert examsession.get_default_question_count(connection) == 30
    assert examsession.get_auto_reveal(connection) is True


# ---------------------------------------------------------------------------
# Partial credit: a partially correct answer earns 0.34 of a point, for answers
# graded from now on. Answers graded earlier keep the score they had.
# ---------------------------------------------------------------------------


def tally_of(correct: int, partial: int, incorrect: int) -> examsession.Tally:
    """A tally scored by the current rule, as if every answer were graded now."""
    return examsession.Tally(
        correct=correct,
        partial=partial,
        incorrect=incorrect,
        credit=100 * correct + examsession.PARTIAL_CREDIT_PERCENT * partial,
    )


def test_the_users_example_scores_eight_point_three_four_of_ten():
    """8 correct + 1 partial + 1 incorrect of 10 is 8.34 points, 83.4%."""
    tally = tally_of(8, 1, 1)

    assert tally.total == 10
    assert tally.points == pytest.approx(8.34)
    assert tally.score_percent == pytest.approx(83.4)
    assert tally.passed is True


@pytest.mark.parametrize(
    ("correct", "partial", "incorrect", "points", "percent"),
    [
        (2, 1, 0, 2.34, 78.0),  # 3 questions
        (1, 1, 1, 1.34, 44.6667),
        (3, 2, 0, 3.68, 73.6),  # 5 questions
        (0, 5, 0, 1.70, 34.0),
        (8, 1, 1, 8.34, 83.4),  # 10 questions
        (5, 10, 5, 8.40, 42.0),  # 20 questions
        (13, 2, 5, 13.68, 68.4),
        (0, 20, 0, 6.80, 34.0),
    ],
)
def test_score_scales_with_the_size_of_the_set(correct, partial, incorrect, points, percent):
    """One question is worth 100/N percentage points, whatever N is."""
    tally = tally_of(correct, partial, incorrect)

    assert tally.points == pytest.approx(points)
    assert tally.score_percent == pytest.approx(percent, abs=1e-3)


def test_three_partial_answers_are_worth_slightly_more_than_one_correct():
    """The rule is 0.34 exactly, so three partials make 1.02 points."""
    assert tally_of(0, 3, 0).points == pytest.approx(1.02)
    assert tally_of(1, 0, 2).points == pytest.approx(1.0)


def test_the_pass_threshold_applies_to_the_score():
    """Partials can tip a session over the line, and exactly 70% passes."""
    assert tally_of(7, 0, 3).score_percent == 70.0
    assert tally_of(7, 0, 3).passed is True
    assert tally_of(6, 3, 1).score_percent == pytest.approx(70.2)
    assert tally_of(6, 3, 1).passed is True
    assert tally_of(6, 2, 2).score_percent == pytest.approx(66.8)
    assert tally_of(6, 2, 2).passed is False


def test_an_empty_tally_scores_nothing_and_does_not_pass():
    tally = examsession.Tally()

    assert tally.points == 0.0
    assert tally.score_percent == 0.0
    assert tally.passed is False


def test_a_new_partial_answer_is_stored_with_its_credit(connection):
    """The credit is fixed when the answer is graded, not derived later."""
    runner = examsession.start_session(connection, 3, random.Random(30))
    play(
        runner,
        [
            examsession.GRADE_PARTIAL,
            examsession.GRADE_CORRECT,
            examsession.GRADE_INCORRECT,
        ],
    )

    detail = examsession.get_session_detail(connection, runner.session.id)

    assert [answer.credit for answer in detail.answers] == [34, 100, 0]
    assert detail.summary.tally.points == pytest.approx(1.34)
    assert detail.summary.tally.score_percent == pytest.approx(44.667, abs=1e-3)


def test_a_session_of_ten_uses_the_partial_credit_for_its_verdict(connection):
    """Six correct and three partial pass at 70.2%; without partial credit they would not."""
    runner = examsession.start_session(connection, 10, random.Random(31))
    play(
        runner,
        [examsession.GRADE_CORRECT] * 6
        + [examsession.GRADE_PARTIAL] * 3
        + [examsession.GRADE_INCORRECT],
    )

    summary = examsession.list_sessions(connection)[0]
    assert summary.tally.score_percent == pytest.approx(70.2)
    assert summary.tally.passed is True
    assert examsession.get_overview(connection).passed_sessions == 1


def test_a_session_graded_partly_under_the_old_rule_keeps_the_old_score(connection):
    """
    A session in flight when the rule changed is scored answer by answer.

    The first answer is rewritten to credit 0, which is what the old rule gave
    a partial answer; the second is graded now. Only the second earns 0.34.
    """
    runner = examsession.start_session(connection, 3, random.Random(32))
    runner.grade(examsession.GRADE_PARTIAL)
    connection.execute("UPDATE session_answers SET credit = 0 WHERE position = 1")
    connection.commit()
    runner.grade(examsession.GRADE_PARTIAL)
    runner.grade(examsession.GRADE_CORRECT)
    runner.finish()

    tally = examsession.get_session_detail(connection, runner.session.id).summary.tally

    assert tally.credit == 0 + 34 + 100
    assert tally.points == pytest.approx(1.34)


def test_a_database_from_before_partial_credit_is_not_rescored(tmp_path):
    """
    Migrating an existing database must leave past sessions exactly as they were.

    The old rule gave only a fully correct answer any credit, so the backfill
    gives correct 100 and everything else 0: a past session of 7 correct,
    2 partial and 1 incorrect is still 70.0% and still a pass - not the 76.8%
    the new rule would give it.
    """
    conn = get_connection(tmp_path / "old.sqlite")
    questionbank.ensure_schema(conn)
    for number in range(1, 11):
        questionbank.upsert_question(conn, make_question(number))
    # The examsession tables exactly as schema v2 left them: no credit column.
    conn.executescript(
        """
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
            finished_at TEXT, planned_count INTEGER NOT NULL, status TEXT NOT NULL
        );
        CREATE TABLE session_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            question_id INTEGER NOT NULL, position INTEGER NOT NULL,
            grade TEXT NOT NULL, answered_at TEXT NOT NULL,
            UNIQUE(session_id, question_id)
        );
        CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE session_questions (
            session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            position INTEGER NOT NULL, question_id INTEGER NOT NULL,
            PRIMARY KEY (session_id, position)
        );
        INSERT INTO schema_version (component, version) VALUES ('examsession', 2);
        INSERT INTO sessions (started_at, finished_at, planned_count, status)
            VALUES ('2026-09-01T10:00:00+00:00', '2026-09-01T10:30:00+00:00', 10, 'completed');
        """
    )
    grades = ["correct"] * 7 + ["partial"] * 2 + ["incorrect"]
    for position, grade in enumerate(grades, start=1):
        conn.execute(
            "INSERT INTO session_answers (session_id, question_id, position, grade, answered_at) "
            "VALUES (1, ?, ?, ?, '2026-09-01T10:10:00+00:00')",
            (position, position, grade),
        )
    conn.commit()

    assert examsession.ensure_schema(conn) == 3

    tally = examsession.list_sessions(conn)[0].tally
    assert tally.credit == 700
    assert tally.points == 7.0
    assert tally.score_percent == 70.0
    assert tally.passed is True
    assert (tally.correct, tally.partial, tally.incorrect) == (7, 2, 1)

    # A session graded after the migration uses the new rule.
    runner = examsession.start_session(conn, 1, random.Random(33))
    runner.grade(examsession.GRADE_PARTIAL)
    newest = examsession.list_sessions(conn)[0].tally
    assert newest.credit == 34
    conn.close()
