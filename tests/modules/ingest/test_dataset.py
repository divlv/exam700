"""
Checks against the built dataset itself.

The unit tests prove the rules work on synthetic rows; these prove the real build
produced what the exam application expects. They are skipped when the dataset has
not been built, so a fresh clone still has a green suite.
"""

from __future__ import annotations

import random

import pytest

from api.modules.questionbank import api as questionbank
from api.modules.shared.api import DATA_DIR, DB_PATH, get_connection

#: Totals established when the corpus was extracted and verified by eye.
EXPECTED_QUESTIONS = 369
EXPECTED_WITHOUT_ANSWER = 60

#: Question 38 prints "Correct Answer:" with nothing after it; question 6 gives
#: its answer as a marked-up Answer Area image, which is perfectly usable.
QUESTION_WITHOUT_ANSWER = 38
QUESTION_WITH_IMAGE_ANSWER = 6


@pytest.fixture(scope="module")
def catalog():
    """The real catalog, or a skip when the dataset has not been built."""
    if not DB_PATH.is_file():
        pytest.skip("dataset not built - run tools/build_dataset.py")

    conn = get_connection()
    questionbank.ensure_schema(conn)
    if questionbank.count(conn) == 0:
        conn.close()
        pytest.skip("catalog is empty - run tools/build_dataset.py")
    yield conn
    conn.close()


def test_catalog_holds_every_question(catalog):
    """All 369 questions, numbered without gaps."""
    questions = questionbank.list_questions(catalog)

    assert len(questions) == EXPECTED_QUESTIONS
    assert [q.id for q in questions] == list(range(1, EXPECTED_QUESTIONS + 1))


def test_questions_without_an_answer_are_marked(catalog):
    """The build must record which questions cannot be self-graded."""
    unanswered = questionbank.list_unanswered(catalog)

    assert len(unanswered) == EXPECTED_WITHOUT_ANSWER
    assert QUESTION_WITHOUT_ANSWER in {q.id for q in unanswered}
    assert all(question.has_answer is False for question in unanswered)


def test_available_count_excludes_them(catalog):
    """The session-size ceiling is the available count, not the catalog size."""
    available = questionbank.count_available(catalog)
    flagged = len(questionbank.list_broken(catalog))

    assert available == EXPECTED_QUESTIONS - EXPECTED_WITHOUT_ANSWER - flagged
    assert questionbank.is_available(catalog, QUESTION_WITHOUT_ANSWER) is False


def test_image_answers_stay_usable(catalog):
    """
    An answer given as a picture is still an answer.

    138 questions answer with a marked-up Answer Area rather than a letter; only
    a completely empty answer disqualifies a question.
    """
    question = questionbank.get_question(catalog, QUESTION_WITH_IMAGE_ANSWER)

    assert question is not None
    assert question.has_answer is True


def test_no_unanswerable_question_is_ever_drawn(catalog):
    """Repeated large draws must never surface an excluded question."""
    excluded = {q.id for q in questionbank.list_unanswered(catalog)}
    size = min(50, questionbank.count_available(catalog))

    for seed in range(20):
        drawn = questionbank.sample_questions(catalog, size, random.Random(seed))
        assert excluded.isdisjoint({question.id for question in drawn})


def test_every_available_question_has_both_images_on_disk(catalog):
    """The application only shows images, so both files must exist."""
    missing = [
        (question.id, path)
        for question in questionbank.list_questions(catalog)
        for path in (question.question_image, question.answer_image)
        if not (DATA_DIR / path).is_file()
    ]

    assert missing == []
