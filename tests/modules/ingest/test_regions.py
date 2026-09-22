"""
Tests for content classification and region building.

These cover the two failure modes that would quietly corrupt the dataset: losing
part of a question because it was mistaken for site furniture, and losing the
part of a question that continues on the next page.
"""

from __future__ import annotations

import pytest

from api.modules.ingest.domain import MARKER_QUESTION, Band, Marker, Region
from api.modules.ingest.internal._content import (
    CONTENT_COLOR,
    PageIndex,
    build_region,
    classify_page,
    is_content_line,
)
from api.modules.ingest.internal._layout import TextLine, extract_glyphs, find_markers, group_lines
from api.modules.ingest.internal._pdf_objects import load_pages
from api.modules.shared.api import SOURCE_DIR

#: Colours measured across the corpus that must never count as question content.
FURNITURE_COLORS = [
    (0.047, 0.039, 0.035),   # advert text
    (1.0, 1.0, 1.0),         # advert placeholder block
    (0.137, 0.129, 0.125),   # comment section
    (0.341, 0.325, 0.306),   # navigation and the Answer / Discussion bar
    (0.141, 0.129, 0.125),   # breadcrumb bar
    (0.471, 0.443, 0.424),   # footer heading
]


def source_pdf(suffix: str):
    """Return the path of one source file, skipping the test if it is absent."""
    path = SOURCE_DIR / f"az-700_exam_questions_with_answers_{suffix}.pdf"
    if not path.is_file():
        pytest.skip(f"source file {path.name} is not available")
    return path


def make_line(color) -> TextLine:
    """Build a minimal line carrying only the colour, for classification tests."""
    return TextLine(
        glyphs=(),
        baseline=100.0,
        cap_height=10.0,
        x0=86.0,
        x1=400.0,
        top=90.0,
        bottom=100.0,
        color=color,
    )


def test_only_the_content_colour_counts_as_content():
    """Furniture is told apart by fill colour, not by indentation."""
    assert is_content_line(make_line(CONTENT_COLOR))
    for color in FURNITURE_COLORS:
        assert not is_content_line(make_line(color)), color
    assert not is_content_line(make_line(None))


def test_indented_answer_options_are_kept_as_content():
    """
    Options are indented rows with radio buttons starting at x ~ 118.

    An earlier left-edge rule dropped them all, so this pins the behaviour: the
    question for file 01 has four options below the question text and they must
    survive classification.
    """
    page = load_pages(source_pdf("01"))[0]
    option_lines = [
        line
        for line in group_lines(extract_glyphs(page))
        if 110 <= line.x0 < 130 and is_content_line(line)
    ]

    assert len(option_lines) >= 2, "indented options must be classified as content"


def test_advert_between_question_and_options_is_cut_out():
    """
    The first question of file 01 continues onto page 2 past an advert.

    The resulting region must therefore have more than one band and must not
    cover the advert, which sits at stream y 206..246 on page index 1.
    """
    pages = load_pages(source_pdf("01"))
    index = PageIndex(pages)
    markers = [marker for page in pages for marker in find_markers(page)]

    region = build_region(index, markers[0], markers[1])

    assert len(region.bands) >= 2, "question spans a page boundary"
    assert {band.page_index for band in region.bands} == {0, 1}
    for band in region.bands:
        if band.page_index == 1:
            assert not (band.y_from < 246 and 206 < band.y_to), (
                f"band {band} overlaps the advert block"
            )


def test_regions_never_reach_past_the_next_marker():
    """A region must stop before the marker that opens the next one."""
    pages = load_pages(source_pdf("01"))
    index = PageIndex(pages)
    markers = [marker for page in pages for marker in find_markers(page)]

    for current, following in zip(markers, markers[1:]):
        region = build_region(index, current, following)
        for band in region.bands:
            assert (band.page_index, band.y_from) >= (current.page_index, 0)
            assert (band.page_index, band.y_to) <= (
                following.page_index,
                following.top,
            )


def test_last_answer_stops_before_the_comment_section():
    """
    The final answer of a file runs to the end of the document.

    The comment section that follows is furniture, so the region must not extend
    into the pages that hold only comments.
    """
    pages = load_pages(source_pdf("01"))
    index = PageIndex(pages)
    markers = [marker for page in pages for marker in find_markers(page)]

    region = build_region(index, markers[-1], None)

    assert region.bands
    last_page = max(band.page_index for band in region.bands)
    assert last_page < len(pages) - 1, "should not reach the trailing comment pages"


def test_every_region_of_a_file_is_non_empty():
    """No question or answer may come out empty, for any of the sample files."""
    for suffix in ("01", "45", "74"):
        pages = load_pages(source_pdf(suffix))
        index = PageIndex(pages)
        markers = [marker for page in pages for marker in find_markers(page)]

        for position, marker in enumerate(markers):
            following = markers[position + 1] if position + 1 < len(markers) else None
            region = build_region(index, marker, following)
            assert region.bands, f"{suffix}: empty region at {marker}"
            assert region.total_height > 0


def test_classify_page_separates_content_from_furniture():
    """Both buckets are populated on a page that carries an advert."""
    page = load_pages(source_pdf("01"))[1]
    content, furniture = classify_page(page)

    assert content, "page should hold question content"
    assert furniture, "page should hold an advert block"
    assert content == sorted(content)
    assert furniture == sorted(furniture)


def test_region_serialises_its_bands_for_storage():
    """Geometry is stored so images can be re-rendered without re-parsing."""
    region = Region(
        bands=(Band(page_index=0, y_from=10.0, y_to=20.0), Band(1, 30.0, 40.0))
    )

    assert region.to_json() == [[0, 10.0, 20.0], [1, 30.0, 40.0]]
    assert region.total_height == 20.0
    assert region.page_span == (0, 1)


def test_marker_position_orders_across_pages():
    """Markers sort by page first, then by vertical position."""
    first = Marker(MARKER_QUESTION, page_index=0, top=900.0, baseline=910.0, x0=87.0)
    second = Marker(MARKER_QUESTION, page_index=1, top=100.0, baseline=110.0, x0=87.0)

    assert first.position < second.position
