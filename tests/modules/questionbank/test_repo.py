"""
Tests for the question catalog.

The catalog is the contract between the build step and the exam application, so
these cover the guarantees the application depends on: rebuilds do not duplicate
rows, geometry survives a round trip, and a session never draws the same question
twice.
"""

from __future__ import annotations

import random
from dataclasses import replace

import pytest

from api.modules.questionbank import api as questionbank
from api.modules.shared.api import get_connection


@pytest.fixture()
def connection(tmp_path):
    """An empty catalog in a throw-away database."""
    conn = get_connection(tmp_path / "test.sqlite")
    questionbank.ensure_schema(conn)
    yield conn
    conn.close()


def make_question(number: int) -> questionbank.Question:
    """Build a catalog row for the given question number."""
    return questionbank.Question(
        id=number,
        source_file=f"az-700_exam_questions_with_answers_{(number - 1) // 5 + 1:02d}.pdf",
        start_page=0,
        end_page=1,
        question_image=f"images/q{number:04d}_question.png",
        answer_image=f"images/q{number:04d}_answer.png",
        geometry={"dpi": 200, "question_bands": [[0, 100.0, 200.0]],
                  "answer_bands": [[1, 50.0, 90.0]]},
    )


def test_schema_migration_is_idempotent(connection):
    """Running the migration again is a no-op, as start-up depends on."""
    version = questionbank.ensure_schema(connection)

    assert version == 2, "v2 adds has_answer and the broken_questions table"
    assert questionbank.ensure_schema(connection) == version


def test_upsert_replaces_instead_of_duplicating(connection):
    """A rebuild must leave one row per question, with the newer values."""
    questionbank.upsert_question(connection, make_question(1))
    questionbank.upsert_question(connection, make_question(1))

    assert questionbank.count(connection) == 1

    changed = questionbank.Question(
        id=1,
        source_file="other.pdf",
        start_page=3,
        end_page=4,
        question_image="images/new_q.png",
        answer_image="images/new_a.png",
        geometry={"dpi": 150},
    )
    questionbank.upsert_question(connection, changed)

    stored = questionbank.get_question(connection, 1)
    assert questionbank.count(connection) == 1
    assert stored.source_file == "other.pdf"
    assert stored.start_page == 3
    assert stored.geometry == {"dpi": 150}


def test_geometry_survives_a_round_trip(connection):
    """Band coordinates must come back unchanged so images can be re-rendered."""
    original = make_question(7)
    questionbank.upsert_question(connection, original)

    stored = questionbank.get_question(connection, 7)
    assert stored.geometry == original.geometry
    assert stored.created_at, "created_at should be stamped on write"


def test_get_returns_none_for_an_unknown_question(connection):
    """Callers can probe the catalog without handling an exception."""
    assert questionbank.get_question(connection, 999) is None


def test_list_questions_is_ordered_by_number(connection):
    """The report and the statistics screens rely on catalog order."""
    for number in (5, 1, 3, 2, 4):
        questionbank.upsert_question(connection, make_question(number))

    assert [q.id for q in questionbank.list_questions(connection)] == [1, 2, 3, 4, 5]


def test_sample_draws_distinct_questions(connection):
    """A session must never show the same question twice."""
    for number in range(1, 21):
        questionbank.upsert_question(connection, make_question(number))

    drawn = questionbank.sample_questions(connection, 10, random.Random(1))

    assert len(drawn) == 10
    assert len({question.id for question in drawn}) == 10


def test_sample_is_reproducible_for_a_seeded_generator(connection):
    """Seeding the generator makes a session reproducible, which tests rely on."""
    for number in range(1, 21):
        questionbank.upsert_question(connection, make_question(number))

    first = questionbank.sample_questions(connection, 5, random.Random(42))
    second = questionbank.sample_questions(connection, 5, random.Random(42))

    assert [q.id for q in first] == [q.id for q in second]


def test_sample_rejects_impossible_sizes(connection):
    """Asking for more questions than exist is a caller error, not a short list."""
    for number in range(1, 6):
        questionbank.upsert_question(connection, make_question(number))

    with pytest.raises(ValueError):
        questionbank.sample_questions(connection, 6)
    with pytest.raises(ValueError):
        questionbank.sample_questions(connection, 0)


def test_delete_all_empties_the_catalog(connection):
    """Used when rebuilding from scratch."""
    for number in range(1, 4):
        questionbank.upsert_question(connection, make_question(number))

    assert questionbank.delete_all_questions(connection) == 3
    assert questionbank.count(connection) == 0


def test_has_answer_survives_a_round_trip(connection):
    """The build's verdict on whether an answer exists must persist."""
    questionbank.upsert_question(
        connection, replace(make_question(1), has_answer=False)
    )
    questionbank.upsert_question(connection, make_question(2))

    assert questionbank.get_question(connection, 1).has_answer is False
    assert questionbank.get_question(connection, 2).has_answer is True


def test_questions_without_an_answer_are_never_drawn(connection):
    """
    Sixty questions print the label with nothing after it.

    They cannot be self-graded, so a session must never see them however many
    times it draws.
    """
    for number in range(1, 11):
        questionbank.upsert_question(
            connection, replace(make_question(number), has_answer=number > 5)
        )

    assert questionbank.count(connection) == 10
    assert questionbank.count_available(connection) == 5
    assert questionbank.available_question_ids(connection) == [6, 7, 8, 9, 10]

    for seed in range(20):
        drawn = questionbank.sample_questions(connection, 5, random.Random(seed))
        assert all(question.id > 5 for question in drawn)

    assert [q.id for q in questionbank.list_unanswered(connection)] == [1, 2, 3, 4, 5]
    assert questionbank.is_available(connection, 1) is False
    assert questionbank.is_available(connection, 6) is True


def test_flagged_questions_drop_out_of_the_draw(connection):
    """A question the user flagged as badly cropped must not come back."""
    for number in range(1, 11):
        questionbank.upsert_question(connection, make_question(number))

    questionbank.mark_broken(connection, 3)
    questionbank.mark_broken(connection, 7)

    assert questionbank.count_available(connection) == 8
    assert questionbank.is_available(connection, 3) is False

    for seed in range(20):
        drawn = questionbank.sample_questions(connection, 8, random.Random(seed))
        assert {3, 7}.isdisjoint({question.id for question in drawn})


def test_flags_survive_a_dataset_rebuild(connection):
    """
    Regression: a rebuild upserts every column of ``questions``.

    The flags therefore live in their own table; if they were a column they
    would be silently wiped by the next build.
    """
    questionbank.upsert_question(connection, make_question(1))
    questionbank.mark_broken(connection, 1)

    questionbank.upsert_question(connection, make_question(1))

    assert questionbank.is_available(connection, 1) is False
    assert [mark.question.id for mark in questionbank.list_broken(connection)] == [1]


def test_flags_survive_wiping_the_catalog(connection):
    """Flags outlive even a full catalog delete, and apply again afterwards."""
    questionbank.upsert_question(connection, make_question(1))
    questionbank.mark_broken(connection, 1)

    questionbank.delete_all_questions(connection)
    # Nothing to show while the row is gone, but the flag is still recorded.
    assert questionbank.list_broken(connection) == []

    questionbank.upsert_question(connection, make_question(1))
    assert questionbank.is_available(connection, 1) is False


def test_marking_is_idempotent_and_keeps_the_first_timestamp(connection):
    """Flagging twice must not duplicate the row or move the timestamp."""
    questionbank.upsert_question(connection, make_question(1))

    questionbank.mark_broken(connection, 1)
    first = questionbank.list_broken(connection)[0].marked_at
    questionbank.mark_broken(connection, 1)

    marks = questionbank.list_broken(connection)
    assert len(marks) == 1
    assert marks[0].marked_at == first


def test_unmark_returns_the_question_to_the_draw(connection):
    """Clearing one flag on the administration screen restores the question."""
    for number in (1, 2):
        questionbank.upsert_question(connection, make_question(number))
    questionbank.mark_broken(connection, 1)

    assert questionbank.unmark_broken(connection, 1) is True
    assert questionbank.count_available(connection) == 2
    # Clearing a flag that is not there is not an error, just nothing to do.
    assert questionbank.unmark_broken(connection, 1) is False


def test_clear_broken_removes_every_flag(connection):
    """The administration screen's "clear all" button."""
    for number in range(1, 6):
        questionbank.upsert_question(connection, make_question(number))
    for number in (1, 3, 5):
        questionbank.mark_broken(connection, number)

    assert questionbank.count_available(connection) == 2
    assert questionbank.clear_broken(connection) == 3
    assert questionbank.count_available(connection) == 5
    assert questionbank.list_broken(connection) == []


def test_sample_can_exclude_questions_already_seen(connection):
    """
    Replacing a flagged question must not hand back one from this session.

    The exam screen passes the questions it has already shown.
    """
    for number in range(1, 11):
        questionbank.upsert_question(connection, make_question(number))

    for seed in range(20):
        drawn = questionbank.sample_questions(
            connection, 3, random.Random(seed), exclude={1, 2, 3, 4, 5, 6, 7}
        )
        assert {question.id for question in drawn} <= {8, 9, 10}


def test_sample_size_is_capped_by_availability(connection):
    """The error must talk about what is available, not the catalog size."""
    for number in range(1, 11):
        questionbank.upsert_question(
            connection, replace(make_question(number), has_answer=number > 4)
        )
    questionbank.mark_broken(connection, 10)

    assert questionbank.count_available(connection) == 5
    questionbank.sample_questions(connection, 5)

    with pytest.raises(ValueError, match="only 5 are available"):
        questionbank.sample_questions(connection, 6)

    with pytest.raises(ValueError, match="only 2 are available"):
        questionbank.sample_questions(connection, 3, exclude={5, 6, 7})
