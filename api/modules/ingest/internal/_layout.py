"""
Layout analysis of the source pages: glyph outlines -> text lines -> markers.

The source PDFs have no text layer, so structure is recovered geometrically. Each
glyph is a ``q ... Q`` block whose ``cm`` matrix is a pure translation followed by
a filled outline; the glyph's bounding box is therefore derived from the outline
coordinates plus that translation.

Two facts found while examining all 74 files drive the design:

* The stable within-line coordinate is the **bottom of the glyph bounding box**
  (the baseline): its spread across one line is under 1 unit, while the ``cm``
  translation varies by more than 10 units and cannot be used for grouping.
* Identical characters do **not** share identical outlines - PDF coordinates are
  quantized to 0.01 at absolute positions, so 4013 glyphs on one page produce
  1521 distinct exact outlines. Recognition must therefore compare *relative*
  glyph proportions with a tolerance, never exact hashes.

Markers are found by matching a word's per-glyph proportions against a template.
This reads the PDF's own drawing commands; it is not OCR, and no rasterization is
involved.

Example:
    >>> page = load_pages(pdf)[0]
    >>> lines = group_lines(extract_glyphs(page))
    >>> len(lines)
    22
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from api.modules.ingest.domain import MARKER_ANSWER, MARKER_QUESTION, Marker
from api.modules.ingest.internal._pdf_objects import PdfPage

#: A graphics-state block; one filled glyph outline lives inside each.
_BLOCK = re.compile(r"\nq\n(.*?)\nQ", re.S)

#: The block's transform. Glyph blocks use a pure translation.
_CM = re.compile(
    r"(-?[\d.]+) (-?[\d.]+) (-?[\d.]+) (-?[\d.]+) (-?[\d.]+) (-?[\d.]+) cm"
)

#: Non-stroking colour, e.g. ``0.267 0.251 0.235 rg``.
_RG = re.compile(r"(-?[\d.]+) (-?[\d.]+) (-?[\d.]+) rg")

_NUMBER = re.compile(r"-?\d+\.\d+")

#: Split glyphs into lines when the baseline jumps by more than this fraction of
#: the line's cap height. Descenders sit ~0.25 cap heights below the baseline and
#: consecutive lines are at least ~1.2 cap heights apart, so 0.5 separates them.
_LINE_SPLIT_FRACTION = 0.5

#: Horizontal gap that counts as a word space, as a fraction of cap height.
#: Measured letter spacing stays under 0.2; real spaces exceed 0.4.
_WORD_GAP_FRACTION = 0.30

#: Glyph outlines with fewer coordinate pairs than this are decoration, not text.
_MIN_OUTLINE_POINTS = 4


@dataclass(frozen=True)
class Glyph:
    """
    One rendered character, described only by its box and colour.

    The character itself is unknown - the PDF never names it. Attributes are in
    stream coordinates (y grows downwards from the top of the page).

    Attributes:
        x0: Left edge of the outline.
        x1: Right edge of the outline.
        y0: Top edge of the outline.
        y1: Bottom edge of the outline; equals the baseline for glyphs without a
            descender, which is what line grouping relies on.
        color: Fill colour as an RGB triple in 0..1, or ``None`` if the block
            inherited its colour.
    """

    x0: float
    x1: float
    y0: float
    y1: float
    color: tuple[float, float, float] | None

    @property
    def width(self) -> float:
        """Outline width in stream units."""
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        """Outline height in stream units."""
        return self.y1 - self.y0


@dataclass(frozen=True)
class TextLine:
    """
    A horizontal run of glyphs sharing one baseline.

    Attributes:
        glyphs: The line's glyphs ordered left to right.
        baseline: Bottom of the lowest-sitting glyph; the line's anchor.
        cap_height: Tallest glyph height on the line, used to normalize every
            proportion so that detection survives the small render-scale drift
            between source files.
        x0: Leftmost glyph edge.
        x1: Rightmost glyph edge.
        top: Highest glyph top - the upper bound of the line's ink.
        bottom: Lowest glyph bottom - the lower bound of the line's ink.
        color: Most common fill colour on the line.
    """

    glyphs: tuple[Glyph, ...]
    baseline: float
    cap_height: float
    x0: float
    x1: float
    top: float
    bottom: float
    color: tuple[float, float, float] | None

    def __len__(self) -> int:
        return len(self.glyphs)


def extract_glyphs(page: PdfPage) -> list[Glyph]:
    """
    Collect every glyph outline drawn on a page.

    Blocks whose transform is not a pure translation are skipped: those are the
    page frame, rules and image placements, not text.

    Args:
        page: Page whose content stream has already been inflated.

    Returns:
        Glyphs in content-stream order, with absolute stream coordinates.
    """
    glyphs: list[Glyph] = []

    for block in _BLOCK.findall(page.content):
        matrix = _CM.search(block)
        if not matrix:
            continue

        values = [float(matrix.group(i)) for i in range(1, 7)]
        if tuple(values[:4]) != (1.0, 0.0, 0.0, 1.0):
            continue
        translate_x, translate_y = values[4], values[5]

        tail = block[matrix.end():]

        color_match = _RG.search(tail)
        color = (
            (
                round(float(color_match.group(1)), 3),
                round(float(color_match.group(2)), 3),
                round(float(color_match.group(3)), 3),
            )
            if color_match
            else None
        )

        # Read coordinates from after the colour operator: its three components
        # would otherwise be mistaken for outline points, and anything before it
        # is a clipping path rather than the glyph itself.
        body = tail[color_match.end():] if color_match else tail
        coordinates = [float(v) for v in _NUMBER.findall(body)]
        if len(coordinates) < _MIN_OUTLINE_POINTS:
            continue

        xs = coordinates[0::2]
        ys = coordinates[1::2]
        if not xs or not ys:
            continue

        glyphs.append(
            Glyph(
                x0=translate_x + min(xs),
                x1=translate_x + max(xs),
                y0=translate_y + min(ys),
                y1=translate_y + max(ys),
                color=color,
            )
        )

    return glyphs


def group_lines(glyphs: list[Glyph], max_cap_height: float = 40.0) -> list[TextLine]:
    """
    Group glyphs into text lines by baseline.

    Args:
        glyphs: Glyphs from :func:`extract_glyphs`.
        max_cap_height: Glyphs taller than this are page decoration - large white
            rectangles standing in for ad frames, borders, the page frame - and
            are excluded so they cannot distort a line's cap height.

    Returns:
        Lines ordered top to bottom, each with glyphs ordered left to right.
    """
    candidates = sorted(
        (g for g in glyphs if 0 < g.height <= max_cap_height and g.width > 0),
        key=lambda g: g.y1,
    )

    groups: list[list[Glyph]] = []
    current: list[Glyph] = []

    for glyph in candidates:
        if current:
            cap_height = max(g.height for g in current)
            if glyph.y1 - current[-1].y1 > _LINE_SPLIT_FRACTION * cap_height:
                groups.append(current)
                current = []
        current.append(glyph)

    if current:
        groups.append(current)

    return [_build_line(group) for group in groups]


def _build_line(group: list[Glyph]) -> TextLine:
    """Assemble a :class:`TextLine` from glyphs known to share a baseline."""
    ordered = tuple(sorted(group, key=lambda g: g.x0))

    colors: dict[tuple[float, float, float] | None, int] = {}
    for glyph in ordered:
        colors[glyph.color] = colors.get(glyph.color, 0) + 1
    dominant = max(colors.items(), key=lambda item: item[1])[0]

    return TextLine(
        glyphs=ordered,
        baseline=max(g.y1 for g in ordered),
        cap_height=max(g.height for g in ordered),
        x0=min(g.x0 for g in ordered),
        x1=max(g.x1 for g in ordered),
        top=min(g.y0 for g in ordered),
        bottom=max(g.y1 for g in ordered),
        color=dominant,
    )


def split_words(line: TextLine) -> list[tuple[Glyph, ...]]:
    """
    Split a line into words on horizontal whitespace.

    Args:
        line: Line to split.

    Returns:
        Words in reading order, each a tuple of glyphs.
    """
    threshold = _WORD_GAP_FRACTION * line.cap_height

    words: list[tuple[Glyph, ...]] = []
    current: list[Glyph] = []
    previous_right: float | None = None

    for glyph in line.glyphs:
        if previous_right is not None and glyph.x0 - previous_right > threshold:
            words.append(tuple(current))
            current = []
        current.append(glyph)
        previous_right = glyph.x1

    if current:
        words.append(tuple(current))

    return words


def word_shape(word: tuple[Glyph, ...], cap_height: float) -> tuple[tuple[float, float], ...]:
    """
    Describe a word as scale-invariant glyph proportions.

    Each glyph becomes ``(width / cap_height, height / cap_height)``. Dividing by
    the line's cap height cancels the render-scale drift between source files, so
    the same word yields the same shape in file 01 and in file 74.

    Args:
        word: Glyphs of one word.
        cap_height: Cap height of the line the word belongs to.

    Returns:
        One ``(relative_width, relative_height)`` pair per glyph.
    """
    scale = cap_height or 1.0
    return tuple((g.width / scale, g.height / scale) for g in word)


def shapes_match(
    observed: tuple[tuple[float, float], ...],
    template: tuple[tuple[float, float], ...],
    tolerance: float,
) -> bool:
    """
    Compare an observed word shape with a template.

    Args:
        observed: Shape from :func:`word_shape`.
        template: Reference shape of the expected word.
        tolerance: Maximum allowed absolute difference for any single
            width or height component.

    Returns:
        ``True`` when the lengths match and every component is within tolerance.
    """
    if len(observed) != len(template):
        return False

    return all(
        abs(o_w - t_w) <= tolerance and abs(o_h - t_h) <= tolerance
        for (o_w, o_h), (t_w, t_h) in zip(observed, template)
    )


# ---------------------------------------------------------------------------
# Marker templates
#
# Each template is the median glyph proportion of the word, measured over every
# occurrence in all 74 source files. Letters are annotated for readability only -
# the PDF never states them; they were identified from the proportions
# themselves (the narrow tall 'i' at 0.169, the widest glyph 'w' at 1.031 and the
# tiny ':' at 0.182 are unambiguous).
#
# Observed worst deviation from these medians across the whole corpus:
# Question 0.028, Correct 0.030, "Answer:" 0.017 - hence the 0.06/0.08
# tolerances below, which separate markers from body text with a wide margin.
# ---------------------------------------------------------------------------

#: Proportions of the word ``Question``: Q u e s t i o n.
_QUESTION_TEMPLATE = (
    (0.817, 1.000),
    (0.564, 0.690),
    (0.620, 0.690),
    (0.550, 0.690),
    (0.380, 0.845),
    (0.169, 0.930),
    (0.620, 0.690),
    (0.564, 0.676),
)

#: Proportions of the word ``Correct``: C o r r e c t.
_CORRECT_TEMPLATE = (
    (0.833, 1.000),
    (0.667, 0.758),
    (0.395, 0.743),
    (0.378, 0.743),
    (0.652, 0.758),
    (0.652, 0.758),
    (0.425, 0.894),
)

#: Proportions of the word ``Answer:``: A n s w e r :.
_ANSWER_TEMPLATE = (
    (0.864, 0.970),
    (0.605, 0.743),
    (0.575, 0.758),
    (1.031, 0.728),
    (0.652, 0.758),
    (0.395, 0.743),
    (0.182, 0.697),
)

_QUESTION_TOLERANCE = 0.06
_ANSWER_TOLERANCE = 0.08

#: Both markers are printed at the left edge of the content column. The range is
#: generous enough to absorb the small render-scale drift between files.
_MARKER_X_RANGE = (78.0, 102.0)

#: ``Question`` is followed by the number, the word ``of`` and the bank total, so
#: the line always has exactly four words. Glyph counts of the fixed words, and
#: the range for the variable question number (1..369 -> one to three digits).
_QUESTION_HEAD_GLYPHS = 8
_QUESTION_OF_GLYPHS = 2
_QUESTION_TOTAL_GLYPHS = 3
_QUESTION_NUMBER_DIGITS = (1, 3)


def _is_question_line(line: TextLine, words: list[tuple[Glyph, ...]]) -> bool:
    """Return whether a line is a ``Question N of NNN`` marker."""
    if len(words) != 4:
        return False

    head, number, of_word, total = words
    min_digits, max_digits = _QUESTION_NUMBER_DIGITS
    if (
        len(head) != _QUESTION_HEAD_GLYPHS
        or not min_digits <= len(number) <= max_digits
        or len(of_word) != _QUESTION_OF_GLYPHS
        or len(total) != _QUESTION_TOTAL_GLYPHS
    ):
        return False

    return shapes_match(
        word_shape(head, line.cap_height), _QUESTION_TEMPLATE, _QUESTION_TOLERANCE
    )


def _is_answer_line(line: TextLine, words: list[tuple[Glyph, ...]]) -> bool:
    """
    Return whether a line is a ``Correct Answer:`` marker.

    The answer letter may sit on the same line ("Correct Answer: B") or be
    absent, so a third word is allowed but not required.
    """
    if len(words) < 2 or len(words[0]) != 7 or len(words[1]) != 7:
        return False

    return shapes_match(
        word_shape(words[0], line.cap_height), _CORRECT_TEMPLATE, _ANSWER_TOLERANCE
    ) and shapes_match(
        word_shape(words[1], line.cap_height), _ANSWER_TEMPLATE, _ANSWER_TOLERANCE
    )


def find_markers(page: PdfPage) -> list[Marker]:
    """
    Locate the question and answer markers on one page.

    Args:
        page: Page whose content stream has been inflated.

    Returns:
        Markers in reading order (top to bottom).

    Note:
        Verified against the whole corpus: this yields 369 question markers and
        369 answer markers over the 74 files, with exactly one answer marker
        between each pair of consecutive question markers.
    """
    markers: list[Marker] = []

    for line in group_lines(extract_glyphs(page)):
        if not _MARKER_X_RANGE[0] <= line.x0 <= _MARKER_X_RANGE[1]:
            continue

        words = split_words(line)
        if not words:
            continue

        if _is_question_line(line, words):
            markers.append(
                Marker(
                    kind=MARKER_QUESTION,
                    page_index=page.index,
                    top=line.top,
                    baseline=line.baseline,
                    x0=line.x0,
                    digit_count=len(words[1]),
                )
            )
        elif _is_answer_line(line, words):
            markers.append(
                Marker(
                    kind=MARKER_ANSWER,
                    page_index=page.index,
                    top=line.top,
                    baseline=line.baseline,
                    x0=line.x0,
                    trailing_words=len(words) - 2,
                )
            )

    markers.sort(key=lambda marker: marker.position)
    return markers


# code-documentation-2026-09-05T18:24:39
