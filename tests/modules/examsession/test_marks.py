"""
Tests for postponing a question ("come back to this") and for returning to
questions out of order.

A postponed question has no grade, so it is not counted; it keeps its place in
the plan, is skipped by the normal pass, and can be opened again by position -
as can an already graded one, whose grade a new one replaces.
"""

from __future__ import annotations

import random

import pytest

from api.modules.examsession import api as examsession
from api.modules.questionbank import api as questionbank
from api.modules.shared.api import get_connection

C, P, I = examsession.GRADE_CORRECT, examsession.GRADE_PARTIAL, examsession.GRADE_INCORRECT


def make_question(number: int) -> questionbank.Question:
    return questionbank.Question(
        id=number,
        source_file="az-700_exam_questions_with_answers_01.pdf",
        start_page=0,
        end_page=1,
        question_image=f"images/q{number:04d}_question.png",
        answer_image=f"images/q{number:04d}_answer.png",
    )


def open_database(path, question_count):
    conn = get_connection(path)
    questionbank.ensure_schema(conn)
    examsession.ensure_schema(conn)
    for number in range(1, question_count + 1):
        questionbank.upsert_question(conn, make_question(number))
    conn.commit()
    return conn


@pytest.fixture()
def connection(tmp_path):
    conn = open_database(tmp_path / "test.sqlite", 10)
    yield conn
    conn.close()


def start(conn, size, seed=1):
    return examsession.start_session(conn, size, random.Random(seed))


def states(runner):
    return [entry.state for entry in runner.entries()]


def test_postponing_a_question_skips_it_without_grading_it(connection):
    runner = start(connection, 5)
    postponed = runner.current()

    runner.mark()

    assert runner.current().id != postponed.id
    assert runner.position == 2
    detail = examsession.get_session_detail(connection, runner.session.id)
    assert detail.answers == ()


def test_the_normal_pass_is_over_when_every_question_is_graded_or_postponed(connection):
    runner = start(connection, 3)
    runner.mark()
    runner.grade(C)
    runner.grade(P)

    assert runner.finished is True
    assert runner.current() is None
    assert runner.has_marked is True
    assert runner.complete is False
    assert runner.unresolved == 1
    assert runner.marked_positions == [1]


def test_a_session_with_nothing_postponed_completes_as_before(connection):
    runner = start(connection, 3)
    for grade in (C, C, I):
        runner.grade(grade)

    assert runner.finished is True
    assert runner.complete is True
    assert runner.unresolved == 0


def test_grading_a_postponed_question_by_position_clears_its_mark_and_counts_it(connection):
    runner = start(connection, 3)
    runner.mark()
    runner.grade(C)
    runner.grade(C)

    runner.grade(I, position=1)

    assert runner.has_marked is False
    assert runner.complete is True
    tally = examsession.get_session_detail(connection, runner.session.id).summary.tally
    assert (tally.correct, tally.incorrect, tally.total) == (2, 1, 3)


def test_grading_an_already_graded_question_replaces_the_grade_and_its_credit(connection):
    runner = start(connection, 2)
    runner.grade(C)
    runner.grade(C)

    runner.grade(I, position=1)

    detail = examsession.get_session_detail(connection, runner.session.id)
    first = next(answer for answer in detail.answers if answer.position == 1)
    assert (first.grade, first.credit) == (I, 0)
    assert detail.summary.tally.correct == 1
    assert detail.summary.tally.points == pytest.approx(1.0)


def test_a_graded_question_cannot_be_postponed(connection):
    runner = start(connection, 2)
    runner.grade(C)

    with pytest.raises(ValueError, match="already graded"):
        runner.mark(position=1)


def test_taking_the_mark_off_returns_the_question_to_the_queue(connection):
    runner = start(connection, 3)
    first = runner.current()
    runner.mark()
    assert runner.current().id != first.id

    runner.unmark(1)

    assert runner.current().id == first.id
    assert runner.has_marked is False


def test_the_next_question_in_the_queue_skips_postponed_ones(connection):
    runner = start(connection, 4)
    runner.mark()  # postpones position 1, so position 2 is current
    current = runner.current()
    upcoming = runner.upcoming()

    assert runner.question_at(2).id == current.id
    assert upcoming.id == runner.question_at(3).id


def test_entries_describe_every_question_for_the_review_screen(connection):
    runner = start(connection, 4)
    runner.grade(P)  # position 1
    runner.mark()  # position 2
    # position 3 pending, position 4 pending

    entries = runner.entries()

    assert [entry.position for entry in entries] == [1, 2, 3, 4]
    assert [entry.state for entry in entries] == [
        examsession.ENTRY_GRADED,
        examsession.ENTRY_MARKED,
        examsession.ENTRY_PENDING,
        examsession.ENTRY_PENDING,
    ]
    assert entries[0].grade == P
    assert entries[1].grade is None
    assert runner.entry_at(9) is None


def test_positions_outside_the_plan_are_refused(connection):
    runner = start(connection, 2)

    assert runner.question_at(0) is None
    assert runner.question_at(3) is None
    with pytest.raises(ValueError):
        runner.grade(C, position=3)
    with pytest.raises(ValueError):
        runner.mark(position=0)


def test_marks_and_out_of_order_grades_survive_a_resume(connection):
    """Graded 1st and 3rd, postponed 2nd: the graded ones are not the first N."""
    runner = start(connection, 4)
    plan = [entry.question_id for entry in runner.entries()]
    runner.grade(C)  # position 1
    runner.mark()  # position 2
    runner.grade(I)  # position 3 (the next in the queue)

    resumed = examsession.resume_session(connection, runner.session.id)

    assert resumed is not None
    assert states(resumed) == [
        examsession.ENTRY_GRADED,
        examsession.ENTRY_MARKED,
        examsession.ENTRY_GRADED,
        examsession.ENTRY_PENDING,
    ]
    assert resumed.marked_positions == [2]
    assert resumed.graded == 2
    assert resumed.current().id == plan[3]
    assert resumed.position == 4


def test_flagging_a_postponed_question_replaces_it_and_clears_the_mark(connection):
    runner = start(connection, 3)
    runner.mark()
    postponed_id = runner.question_at(1).id

    replacement = runner.flag_broken(position=1)

    assert replacement is not None and replacement.id != postponed_id
    assert runner.entry_at(1).state == examsession.ENTRY_PENDING
    resumed = examsession.resume_session(connection, runner.session.id)
    assert resumed.has_marked is False
    assert resumed.question_at(1).id == replacement.id


def test_removing_a_position_moves_the_marks_with_their_questions(tmp_path):
    """With the pool exhausted a flagged question leaves the plan; later marks shift up."""
    conn = open_database(tmp_path / "small.sqlite", 3)
    try:
        runner = examsession.start_session(conn, 3, random.Random(2))
        runner.mark(position=3)

        assert runner.flag_broken(position=1) is None

        assert runner.total == 2
        assert runner.marked_positions == [2]
        resumed = examsession.resume_session(conn, runner.session.id)
        assert resumed.total == 2
        assert resumed.marked_positions == [2]
    finally:
        conn.close()


def test_finishing_counts_postponed_and_untouched_questions_as_incorrect(connection):
    """As in an exam: a question left without an answer is a wrong one."""
    runner = start(connection, 4)
    runner.grade(C)
    runner.grade(P)
    runner.mark()  # postponed, never returned to
    # one question left untouched

    runner.finish()

    detail = examsession.get_session_detail(connection, runner.session.id)
    tally = detail.summary.tally
    assert len(detail.answers) == 4
    assert (tally.correct, tally.partial, tally.incorrect, tally.total) == (1, 1, 2, 4)
    assert tally.points == pytest.approx(1.34)
    assert tally.score_percent == pytest.approx(33.5)
    assert detail.summary.session.planned_count == 4
    assert detail.summary.session.status == examsession.STATUS_COMPLETED
    assert [answer.credit for answer in detail.answers if answer.grade == I] == [0, 0]


def test_the_skipped_questions_are_stored_like_any_other_incorrect_answer(connection):
    runner = start(connection, 3)
    runner.mark()
    postponed_id = runner.question_at(1).id
    runner.grade(C)
    runner.grade(C)

    runner.finish()

    detail = examsession.get_session_detail(connection, runner.session.id)
    skipped = next(a for a in detail.answers if a.question_id == postponed_id)
    assert (skipped.grade, skipped.position, skipped.counted) == (I, 1, True)
    assert postponed_id in {a.question_id for a in detail.mistakes}


def test_finishing_a_fully_graded_session_adds_nothing(connection):
    runner = start(connection, 3)
    for grade in (C, P, I):
        runner.grade(grade)

    runner.finish()

    detail = examsession.get_session_detail(connection, runner.session.id)
    assert [a.grade for a in detail.answers] == [C, P, I]


def test_abandoning_keeps_only_the_grades_given(connection):
    """Leaving is not finishing: the questions not answered are not held against the user."""
    runner = start(connection, 4)
    runner.grade(C)
    runner.mark()

    runner.abandon()

    detail = examsession.get_session_detail(connection, runner.session.id)
    assert len(detail.answers) == 1
    assert detail.summary.tally.total == 1
    assert detail.summary.session.status == examsession.STATUS_ABANDONED


def test_a_skipped_question_is_practised_again_first(tmp_path):
    """Counted as incorrect, it is in the answer history, so selection prefers it next time."""
    conn = open_database(tmp_path / "small.sqlite", 2)
    try:
        first = examsession.start_session(conn, 2, random.Random(1))
        answered_id = first.current().id
        first.grade(C)
        skipped_id = next(e.question_id for e in first.entries() if e.question_id != answered_id)
        first.finish()  # the other question was never answered

        picks = set()
        for seed in range(20):
            runner = examsession.start_session(conn, 1, random.Random(seed))
            picks.add(runner.current().id)
            runner.abandon()

        assert picks == {skipped_id}
    finally:
        conn.close()


def test_a_database_without_the_mark_column_is_migrated_with_no_marks(tmp_path):
    """Sessions that existed before the mark was introduced come out with nothing postponed."""
    conn = get_connection(tmp_path / "v3.sqlite")
    questionbank.ensure_schema(conn)
    for number in range(1, 4):
        questionbank.upsert_question(conn, make_question(number))
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
            grade TEXT NOT NULL, credit INTEGER NOT NULL DEFAULT 0, answered_at TEXT NOT NULL,
            UNIQUE(session_id, question_id)
        );
        CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE session_questions (
            session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            position INTEGER NOT NULL, question_id INTEGER NOT NULL,
            PRIMARY KEY (session_id, position)
        );
        INSERT INTO schema_version (component, version) VALUES ('examsession', 3);
        INSERT INTO sessions (started_at, planned_count, status)
            VALUES ('2026-09-01T10:00:00+00:00', 3, 'in_progress');
        INSERT INTO session_questions (session_id, position, question_id)
            VALUES (1, 1, 1), (1, 2, 2), (1, 3, 3);
        INSERT INTO session_answers (session_id, question_id, position, grade, credit, answered_at)
            VALUES (1, 1, 1, 'correct', 100, '2026-09-01T10:05:00+00:00');
        """
    )
    conn.commit()

    assert examsession.ensure_schema(conn) == 4

    runner = examsession.resume_session(conn, 1)
    assert runner is not None
    assert states(runner) == [
        examsession.ENTRY_GRADED,
        examsession.ENTRY_PENDING,
        examsession.ENTRY_PENDING,
    ]
    assert runner.has_marked is False
    runner.mark()
    assert examsession.resume_session(conn, 1).marked_positions == [2]
    conn.close()
