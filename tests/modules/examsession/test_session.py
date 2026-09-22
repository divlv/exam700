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


def test_schema_v2_persists_session_plan(connection):
    """The plan table is created by the same migration run as v1."""
    assert examsession.ensure_schema(connection) == 2


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
