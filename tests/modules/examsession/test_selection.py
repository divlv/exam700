"""
Tests for how the next question is chosen from the user's answer history.

Each time a question is needed, ten random available questions compete. A
question never answered beats any answered one; if all ten were answered, the
one with the most incorrect answers wins; every tie is broken at random.
"""

from __future__ import annotations

import random

import pytest

from api.modules.examsession import api as examsession
from api.modules.questionbank import api as questionbank
from api.modules.shared.api import get_connection

SEEDS = range(40)


@pytest.fixture()
def connection(tmp_path):
    """A throw-away database with the schemas and no questions yet."""
    conn = get_connection(tmp_path / "test.sqlite")
    questionbank.ensure_schema(conn)
    examsession.ensure_schema(conn)
    yield conn
    conn.close()


def add_questions(conn, ids):
    """Put questions with the given numbers into the catalog."""
    for number in ids:
        questionbank.upsert_question(
            conn,
            questionbank.Question(
                id=number,
                source_file="az-700_exam_questions_with_answers_01.pdf",
                start_page=0,
                end_page=1,
                question_image=f"images/q{number:04d}_question.png",
                answer_image=f"images/q{number:04d}_answer.png",
            ),
        )
    conn.commit()


def record_history(conn, history):
    """
    Write answers straight into the journal, as if earlier sessions had happened.

    Args:
        history: ``{question number: [grades]}``. A question's n-th grade goes
            into the n-th session, since a session holds one answer per question.
    """
    rounds = max((len(grades) for grades in history.values()), default=0)
    for round_index in range(rounds):
        cursor = conn.execute(
            "INSERT INTO sessions (started_at, finished_at, planned_count, status) "
            "VALUES ('2026-09-01T10:00:00+00:00', '2026-09-01T10:30:00+00:00', 1, 'completed')"
        )
        session_id = cursor.lastrowid
        for question_id, grades in history.items():
            if round_index < len(grades):
                conn.execute(
                    "INSERT INTO session_answers "
                    "(session_id, question_id, position, grade, credit, answered_at) "
                    "VALUES (?, ?, 1, ?, 0, '2026-09-01T10:10:00+00:00')",
                    (session_id, question_id, grades[round_index]),
                )
    conn.commit()


def first_pick(conn, seed):
    """The single question a one-question session chooses with this seed."""
    runner = examsession.start_session(conn, 1, random.Random(seed))
    chosen = runner.current().id
    runner.abandon()
    return chosen


class RecordingRandom(random.Random):
    """A random source that remembers how many items each ``sample`` call drew."""

    def __init__(self, seed):
        super().__init__(seed)
        self.sample_sizes: list[int] = []

    def sample(self, population, k, **kwargs):
        self.sample_sizes.append(k)
        return super().sample(population, k, **kwargs)


C, P, I = "correct", "partial", "incorrect"


def test_a_question_never_answered_beats_answered_ones(connection):
    add_questions(connection, range(1, 9))
    record_history(connection, {number: [C, I] for number in range(1, 8)})

    assert {first_pick(connection, seed) for seed in SEEDS} == {8}


def test_among_answered_questions_the_one_with_most_incorrect_answers_wins(connection):
    add_questions(connection, range(1, 6))
    record_history(
        connection,
        {1: [C, C], 2: [I], 3: [I, I, I], 4: [I, I], 5: [P, P, P, P]},
    )

    assert {first_pick(connection, seed) for seed in SEEDS} == {3}


def test_the_users_example_question_54_is_worse_than_question_128(connection):
    """54: 1 correct, 2 partial, 3 incorrect. 128: 3 correct."""
    add_questions(connection, [54, 128])
    record_history(connection, {54: [C, P, P, I, I, I], 128: [C, C, C]})

    assert {first_pick(connection, seed) for seed in SEEDS} == {54}


def test_equally_bad_questions_are_chosen_at_random_and_no_other(connection):
    add_questions(connection, range(1, 6))
    record_history(
        connection,
        {1: [I, I], 2: [C, I, I], 3: [I], 4: [C], 5: [P]},
    )

    picks = {first_pick(connection, seed) for seed in SEEDS}

    assert picks == {1, 2}, "1 and 2 tie on two incorrect answers; nothing else may win"


def test_several_unanswered_questions_are_all_possible_and_answered_never(connection):
    add_questions(connection, range(1, 7))
    record_history(connection, {1: [I, I, I], 2: [I, I]})

    picks = {first_pick(connection, seed) for seed in SEEDS}

    assert picks == {3, 4, 5, 6}


def test_correct_and_partial_answers_are_not_told_apart(connection):
    """Neither has an incorrect answer, so they tie."""
    add_questions(connection, [1, 2])
    record_history(connection, {1: [C, C, C, C], 2: [P]})

    assert {first_pick(connection, seed) for seed in SEEDS} == {1, 2}


def test_ten_random_questions_compete_when_the_pool_is_large(connection):
    add_questions(connection, range(1, 31))
    rng = RecordingRandom(1)

    examsession.start_session(connection, 3, rng).abandon()

    assert rng.sample_sizes == [10, 10, 10]


def test_fewer_than_ten_compete_when_fewer_are_left(connection):
    add_questions(connection, range(1, 13))
    rng = RecordingRandom(2)

    examsession.start_session(connection, 12, rng).abandon()

    # 12 candidates, then 11, then 10 ... down to 1: never more than are left.
    assert rng.sample_sizes == [10, 10, 10] + [9, 8, 7, 6, 5, 4, 3, 2, 1]


def test_a_session_asks_exactly_the_requested_number_of_distinct_questions(connection):
    add_questions(connection, range(1, 31))
    record_history(connection, {number: [I] for number in range(1, 16)})

    runner = examsession.start_session(connection, 20, random.Random(3))
    drawn = []
    while not runner.finished:
        drawn.append(runner.current().id)
        runner.grade(examsession.GRADE_CORRECT)

    assert len(drawn) == 20
    assert len(set(drawn)) == 20


def test_a_session_larger_than_the_pool_is_refused(connection):
    add_questions(connection, range(1, 6))

    with pytest.raises(examsession.NoQuestionsAvailable, match="only 5 are available"):
        examsession.start_session(connection, 6)
    with pytest.raises(examsession.NoQuestionsAvailable):
        examsession.start_session(connection, 0)


def test_the_replacement_for_a_flagged_question_follows_the_same_rule(connection):
    """
    Four questions, three answered. The unanswered one is drawn first; once it
    is flagged, the worst of the remaining three replaces it.
    """
    add_questions(connection, [1, 2, 3, 4])
    record_history(connection, {1: [C], 2: [C, P], 3: [I, I]})

    runner = examsession.start_session(connection, 1, random.Random(4))
    assert runner.current().id == 4

    replacement = runner.flag_broken()

    assert replacement is not None
    assert replacement.id == 3


def test_a_reset_forgets_the_history(connection):
    """After a reset every question is unanswered again, so the old worst is not always first."""
    add_questions(connection, range(1, 5))
    record_history(connection, {1: [C], 2: [C], 3: [C], 4: [I, I]})
    assert {first_pick(connection, seed) for seed in SEEDS} == {4}

    examsession.reset_all_sessions(connection)

    assert len({first_pick(connection, seed) for seed in SEEDS}) > 1


def test_answers_from_abandoned_sessions_count_towards_the_history(connection):
    add_questions(connection, [1, 2])
    runner = examsession.start_session(connection, 2, random.Random(5))
    first = runner.current().id
    runner.grade(examsession.GRADE_INCORRECT)
    runner.abandon()

    other = 2 if first == 1 else 1
    # `other` was never answered, `first` was answered wrongly in an abandoned session.
    assert {first_pick(connection, seed) for seed in SEEDS} == {other}
