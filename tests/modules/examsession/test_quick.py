"""
Tests for the "quick question" mode: one question asked on its own.

It is chosen by the same rules as any session's questions and graded like any
other answer, so its grade counts in the overall answer figures. It is not a
session of its own, though: it stays out of the session list and the session
counts, where a one-question "pass" would only distort them.
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


def ask_quick(conn, grade, seed=1):
    runner = examsession.start_quick_question(conn, random.Random(seed))
    runner.grade(grade)
    runner.finish()
    return runner


def test_a_quick_question_is_a_one_question_session_of_its_own_kind(connection):
    runner = examsession.start_quick_question(connection, random.Random(1))

    assert runner.total == 1
    assert runner.session.planned_count == 1
    assert runner.session.kind == examsession.KIND_QUICK
    assert runner.session.status == examsession.STATUS_IN_PROGRESS


def test_a_regular_session_stays_a_test(connection):
    runner = examsession.start_session(connection, 3, random.Random(1))

    assert runner.session.kind == examsession.KIND_TEST


def test_the_question_is_chosen_by_the_general_rules_never_answered_first(tmp_path):
    conn = open_database(tmp_path / "small.sqlite", 3)
    try:
        history = examsession.start_session(conn, 2, random.Random(1))
        answered = {entry.question_id for entry in history.entries()}
        history.grade(C)
        history.grade(C)
        history.finish()
        (never_answered,) = {1, 2, 3} - answered

        picks = set()
        for seed in range(20):
            runner = examsession.start_quick_question(conn, random.Random(seed))
            picks.add(runner.current().id)
            runner.abandon()

        assert picks == {never_answered}
    finally:
        conn.close()


def test_among_answered_questions_it_prefers_the_one_answered_worst(tmp_path):
    conn = open_database(tmp_path / "small.sqlite", 2)
    try:
        history = examsession.start_session(conn, 2, random.Random(1))
        first, second = [entry.question_id for entry in history.entries()]
        history.grade(C)
        history.grade(I)
        history.finish()

        picks = set()
        for seed in range(20):
            runner = examsession.start_quick_question(conn, random.Random(seed))
            picks.add(runner.current().id)
            runner.abandon()

        assert picks == {second}, "the one graded incorrect comes first"
    finally:
        conn.close()


def test_the_grade_counts_in_the_overall_figures(connection):
    test = examsession.start_session(connection, 2, random.Random(2))
    test.grade(C)
    test.grade(C)
    test.finish()

    ask_quick(connection, P)

    overview = examsession.get_overview(connection)
    assert overview.tally.total == 3
    assert (overview.tally.correct, overview.tally.partial, overview.tally.incorrect) == (2, 1, 0)
    assert overview.tally.points == pytest.approx(2.34)
    assert overview.tally.score_percent == pytest.approx(78.0)


def test_a_quick_question_is_not_a_session_in_the_counts(connection):
    test = examsession.start_session(connection, 2, random.Random(2))
    test.grade(C)
    test.grade(C)
    test.finish()
    ask_quick(connection, C)
    ask_quick(connection, I)

    overview = examsession.get_overview(connection)

    assert overview.sessions == 1
    assert overview.completed_sessions == 1
    assert overview.passed_sessions == 1, "a quick correct answer is not a passed session"
    assert overview.quick_questions == 2


def test_quick_questions_are_left_out_of_the_session_list(connection):
    test = examsession.start_session(connection, 2, random.Random(3))
    test.grade(C)
    test.grade(C)
    test.finish()
    quick = ask_quick(connection, C)

    listed = [summary.session.id for summary in examsession.list_sessions(connection)]

    assert listed == [test.session.id]
    assert quick.session.id not in listed


def test_the_result_of_a_quick_question_can_still_be_shown(connection):
    quick = ask_quick(connection, P)

    detail = examsession.get_session_detail(connection, quick.session.id)

    assert detail is not None
    assert detail.summary.session.kind == examsession.KIND_QUICK
    assert [answer.grade for answer in detail.answers] == [P]
    assert detail.summary.tally.points == pytest.approx(0.34)


def test_an_unfinished_quick_question_can_be_resumed(connection):
    started = examsession.start_quick_question(connection, random.Random(4))

    active = examsession.get_active_session(connection)
    resumed = examsession.resume_session(connection, active.id)

    assert active.kind == examsession.KIND_QUICK
    assert resumed.current().id == started.current().id


def test_resetting_all_statistics_removes_the_quick_answers_too(connection):
    ask_quick(connection, C)
    ask_quick(connection, I)

    examsession.reset_all_sessions(connection)

    overview = examsession.get_overview(connection)
    assert overview.tally.total == 0
    assert overview.quick_questions == 0


def test_sessions_stored_before_the_kind_existed_are_regular_tests(tmp_path):
    """A database from before quick questions: every stored session is a test."""
    conn = get_connection(tmp_path / "v4.sqlite")
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
            marked INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (session_id, position)
        );
        INSERT INTO schema_version (component, version) VALUES ('examsession', 4);
        INSERT INTO sessions (started_at, finished_at, planned_count, status)
            VALUES ('2026-09-01T10:00:00+00:00', '2026-09-01T10:30:00+00:00', 2, 'completed');
        INSERT INTO session_answers (session_id, question_id, position, grade, credit, answered_at)
            VALUES (1, 1, 1, 'correct', 100, '2026-09-01T10:05:00+00:00'),
                   (1, 2, 2, 'correct', 100, '2026-09-01T10:06:00+00:00');
        """
    )
    conn.commit()

    assert examsession.ensure_schema(conn) == 5

    listed = examsession.list_sessions(conn)
    assert [s.session.kind for s in listed] == [examsession.KIND_TEST]
    assert examsession.get_overview(conn).sessions == 1
    conn.close()
