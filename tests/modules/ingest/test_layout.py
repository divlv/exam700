"""
Tests for marker detection against the real source PDFs.

Synthetic fixtures would prove nothing here: the whole point of the detector is
that it copes with the actual artwork, where every glyph is a vector outline and
the render scale drifts slightly between files. The files chosen cover that
drift and every question-number width (one, two and three digits).
"""

from __future__ import annotations

import pytest

from api.modules.ingest.domain import MARKER_ANSWER, MARKER_QUESTION
from api.modules.ingest.internal._layout import (
    extract_glyphs,
    find_markers,
    group_lines,
    split_words,
)
from api.modules.ingest.internal._pdf_objects import PdfStructureError, load_pages
from api.modules.shared.api import SOURCE_DIR

#: file name suffix -> (pages, questions, digits in the question numbers)
SAMPLE_FILES = {
    "01": (7, 5, 1),
    "20": (20, 5, None),   # spans the 99 -> 100 transition, so digits vary
    "45": (7, 5, 3),
    "74": (8, 4, 3),       # the last file holds only four questions
}


def source_pdf(suffix: str):
    """Return the path of one source file, skipping the test if it is absent."""
    path = SOURCE_DIR / f"az-700_exam_questions_with_answers_{suffix}.pdf"
    if not path.is_file():
        pytest.skip(f"source file {path.name} is not available")
    return path


@pytest.mark.parametrize("suffix", sorted(SAMPLE_FILES))
def test_pages_load_with_expected_geometry(suffix):
    """Every page is US Letter and opens with the flip-and-scale transform."""
    expected_pages = SAMPLE_FILES[suffix][0]
    pages = load_pages(source_pdf(suffix))

    assert len(pages) == expected_pages
    for page in pages:
        assert (page.width_pt, page.height_pt) == (612.0, 792.0)
        assert page.base_matrix == (0.75, 0.0, 0.0, -0.75, 0.0, 792.0)
        assert page.content, "content stream should not be empty"


@pytest.mark.parametrize("suffix", sorted(SAMPLE_FILES))
def test_markers_alternate_question_then_answer(suffix):
    """Each file yields the expected question count, strictly alternating."""
    expected_questions = SAMPLE_FILES[suffix][1]
    pages = load_pages(source_pdf(suffix))
    markers = [marker for page in pages for marker in find_markers(page)]

    kinds = [marker.kind for marker in markers]
    assert kinds == [MARKER_QUESTION, MARKER_ANSWER] * expected_questions


@pytest.mark.parametrize("suffix", sorted(SAMPLE_FILES))
def test_question_markers_report_their_digit_count(suffix):
    """The digit count is what pins the numbering, so it must be plausible."""
    expected_digits = SAMPLE_FILES[suffix][2]
    pages = load_pages(source_pdf(suffix))
    markers = [
        marker
        for page in pages
        for marker in find_markers(page)
        if marker.kind == MARKER_QUESTION
    ]

    assert markers
    for marker in markers:
        assert 1 <= marker.digit_count <= 3
        if expected_digits is not None:
            assert marker.digit_count == expected_digits


def test_markers_sit_at_the_left_edge_of_the_content_column():
    """Both marker kinds are printed flush left, which the detector relies on."""
    pages = load_pages(source_pdf("01"))
    markers = [marker for page in pages for marker in find_markers(page)]

    assert markers
    for marker in markers:
        assert 78.0 <= marker.x0 <= 102.0


def test_most_glyphs_of_a_line_share_one_baseline():
    """
    Line grouping keys on the glyph bottom, so that value must cluster tightly.

    Not every glyph lands on it - descenders drop below and list bullets sit
    above - but the bulk of a line must agree, which is what makes the bottom
    usable as an anchor. The transform translation is not: it varies by more than
    ten units across a single line.
    """
    page = load_pages(source_pdf("01"))[0]
    lines = [line for line in group_lines(extract_glyphs(page)) if len(line) >= 10]

    assert lines
    for line in lines:
        bottoms = sorted(glyph.y1 for glyph in line.glyphs)
        median = bottoms[len(bottoms) // 2]
        on_baseline = sum(1 for value in bottoms if abs(value - median) <= 0.5)

        assert on_baseline / len(bottoms) >= 0.7, (
            f"only {on_baseline}/{len(bottoms)} glyphs share the baseline"
        )


def test_question_marker_splits_into_four_words():
    """``Question N of NNN`` must split as head, number, ``of``, total."""
    page = load_pages(source_pdf("01"))[0]
    markers = find_markers(page)
    assert markers

    marker_line = next(
        line
        for line in group_lines(extract_glyphs(page))
        if abs(line.top - markers[0].top) < 0.01
    )
    words = split_words(marker_line)

    assert [len(word) for word in words] == [8, 1, 2, 3]


def test_load_pages_rejects_a_file_that_is_not_a_pdf(tmp_path):
    """A truncated or foreign file must fail loudly rather than yield no pages."""
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\nnot really a pdf\n")

    with pytest.raises(PdfStructureError):
        load_pages(broken)


def test_answer_markers_report_the_words_that_follow_the_label():
    """
    ``trailing_words`` distinguishes a real answer from an empty label.

    Part of this material prints ``Correct Answer:`` with nothing after it, and
    the build has to flag those rather than present an empty answer as valid.
    """
    pages = load_pages(source_pdf("01"))
    answers = [
        marker
        for page in pages
        for marker in find_markers(page)
        if marker.kind == MARKER_ANSWER
    ]

    assert answers
    # Every answer in file 01 does carry at least one answer letter.
    assert all(marker.trailing_words >= 1 for marker in answers)
    assert all(marker.digit_count == 0 for marker in answers)
