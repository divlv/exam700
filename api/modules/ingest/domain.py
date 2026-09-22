"""
Domain model of the extraction stage.

Everything here is expressed in *stream* coordinates: the units used inside a
page's content stream, where y grows downwards from the top of the page. A page
is 816 x 1056 stream units, which maps to 612 x 792 PDF points.

A question is described by two regions - the question itself with its A/B/C/D
options, and the correct answer with its explanation. A region is a list of
:class:`Band` slices rather than one rectangle, because a question or an answer
frequently continues onto the next page and because interleaved site furniture
(adverts, navigation) has to be cut out of the middle.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Marker that opens a question, printed as ``Question N of 369``.
MARKER_QUESTION = "question"

#: Marker that opens the correct answer, printed as ``Correct Answer: X``.
MARKER_ANSWER = "answer"


@dataclass(frozen=True)
class Marker:
    """
    A recognised structural label on a page.

    Attributes:
        kind: Either :data:`MARKER_QUESTION` or :data:`MARKER_ANSWER`.
        page_index: Zero-based page index within the source file.
        top: Top of the marker line's ink, in stream units.
        baseline: Baseline of the marker line, in stream units.
        x0: Left edge of the marker line.
        digit_count: For a question marker, how many digits the printed number
            has (1, 2 or 3). Zero for answer markers. Used to cross-check the
            sequential numbering without having to recognise the digits, which
            this font makes impossible - its digits are tabular and share widths.
        trailing_words: For an answer marker, how many words follow
            ``Correct Answer:`` on the same line - the answer letters. Zero means
            the source printed the label with no answer after it, which happens
            for a number of questions in this material.
    """

    kind: str
    page_index: int
    top: float
    baseline: float
    x0: float
    digit_count: int = 0
    trailing_words: int = 0

    @property
    def position(self) -> tuple[int, float]:
        """Sort key placing markers in reading order across pages."""
        return self.page_index, self.top


@dataclass(frozen=True)
class Band:
    """
    A horizontal slice of one page.

    Attributes:
        page_index: Zero-based page index within the source file.
        y_from: Upper bound in stream units, inclusive.
        y_to: Lower bound in stream units, exclusive.
    """

    page_index: int
    y_from: float
    y_to: float

    @property
    def height(self) -> float:
        """Band height in stream units; zero or negative means empty."""
        return self.y_to - self.y_from

    def overlaps(self, other: Band) -> bool:
        """Return whether two bands are on the same page and intersect."""
        return (
            self.page_index == other.page_index
            and self.y_from < other.y_to
            and other.y_from < self.y_to
        )


@dataclass(frozen=True)
class Region:
    """
    The full extent of a question or of an answer.

    Attributes:
        bands: Page slices in reading order. More than one band means the region
            either crosses a page boundary or has site furniture cut out of it.
    """

    bands: tuple[Band, ...]

    @property
    def total_height(self) -> float:
        """Combined height of all bands, in stream units."""
        return sum(band.height for band in self.bands)

    @property
    def page_span(self) -> tuple[int, int]:
        """First and last page index the region touches."""
        indexes = [band.page_index for band in self.bands]
        return min(indexes), max(indexes)

    def to_json(self) -> list[list[float]]:
        """
        Serialise as plain lists for storage in the question catalog.

        Returns:
            One ``[page_index, y_from, y_to]`` triple per band, so the images can
            be re-rendered at a different resolution without re-parsing the PDF.
        """
        return [[band.page_index, band.y_from, band.y_to] for band in self.bands]


@dataclass(frozen=True)
class ExtractedQuestion:
    """
    One question located in a source file, before its images are rendered.

    Attributes:
        number: Question number as printed, assigned from the marker order.
        source_file: Name of the source PDF.
        question: Region holding the question text and its options.
        answer: Region holding the correct answer and any explanation.
        answer_given: Whether the source actually printed an answer after the
            ``Correct Answer:`` label. A number of questions in this material
            have the label with nothing after it.
    """

    number: int
    source_file: str
    question: Region
    answer: Region
    answer_given: bool = True

    @property
    def page_span(self) -> tuple[int, int]:
        """First and last page index across both regions."""
        q_start, q_end = self.question.page_span
        a_start, a_end = self.answer.page_span
        return min(q_start, a_start), max(q_end, a_end)


# code-documentation-2026-09-05T18:24:39
