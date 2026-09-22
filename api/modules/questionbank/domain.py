"""
Domain model of the question bank.

A question is not stored as text: the source PDFs contain no text layer at all,
so a question is a pair of pre-rendered PNG images (the question with its A/B/C/D
options, and the correct answer with its explanation) plus the geometry those
images were cut from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Grades a user can give themselves. Kept here because both the question bank
#: and the later exam-session module refer to the same vocabulary.
GRADE_CORRECT = "correct"
GRADE_PARTIAL = "partial"
GRADE_INCORRECT = "incorrect"
GRADES = (GRADE_CORRECT, GRADE_PARTIAL, GRADE_INCORRECT)


@dataclass(frozen=True)
class Question:
    """
    One exam question with its prepared images.

    Attributes:
        id: Question number as printed in the source PDF, 1..370. Used as the
            primary key so a rebuild updates rows in place instead of duplicating.
        source_file: File name the question came from, e.g.
            ``az-700_exam_questions_with_answers_01.pdf``.
        start_page: Zero-based index of the page the question marker sits on.
        end_page: Zero-based index of the page the answer region ends on. Differs
            from ``start_page`` whenever a question or answer spans pages.
        question_image: Path of the question PNG, relative to the data directory.
        answer_image: Path of the answer PNG, relative to the data directory.
        geometry: Opaque payload describing the bands the images were cut from,
            kept so images can be re-rendered at another DPI without re-parsing
            the PDF. Produced and interpreted by the ``ingest`` module.
        has_answer: Whether the source PDF actually printed an answer. Sixty of
            the questions carry the ``Correct Answer:`` label with nothing after
            it, so self-grading them is impossible and they are never drawn for a
            session. Derived by the build, therefore overwritten on every rebuild.
        created_at: ISO-8601 timestamp of the last build of this row.
    """

    id: int
    source_file: str
    start_page: int
    end_page: int
    question_image: str
    answer_image: str
    geometry: dict[str, Any] = field(default_factory=dict)
    has_answer: bool = True
    created_at: str = ""


@dataclass(frozen=True)
class BrokenMark:
    """
    A question the user marked as badly cropped.

    Some questions are cut incorrectly because of defects in the source
    material. The user flags those during a session; a flagged question is
    dropped from sampling and from every statistic, until the flag is removed on
    the administration screen.

    Attributes:
        question: The flagged question, so the screen can show its image.
        marked_at: ISO-8601 timestamp of when the flag was set.
    """

    question: Question
    marked_at: str


# code-documentation-2026-09-05T18:24:39
