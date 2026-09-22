"""
Separating question content from site furniture, and turning markers into regions.

The source pages are printed web pages, so besides the question they carry a site
header, a breadcrumb bar, advert blocks, an "Answer / Discussion" button bar and a
comment section. All of that has to be cut away, including when it sits *between*
a question and its answer, which happens for every single question.

The discriminator is the **fill colour**, not the indentation. Colours measured
over the corpus separate cleanly:

===========================  =======  ==========================================
Fill colour                  Lines    What it is
===========================  =======  ==========================================
(0.267, 0.251, 0.235)           5790  question text, options, answer, explanation
(1.0, 1.0, 1.0)                  379  white advert placeholder blocks
(0.047, 0.039, 0.035)            369  advert text (always 17 glyphs at x 118)
(0.137, 0.129, 0.125)            100  comment section
(0.341, 0.325, 0.306)             26  header navigation, "Answer / Discussion" bar
(0.141, 0.129, 0.125)             25  breadcrumb bar
(0.471/0.671 greys)               49  footer headings and muted footer text
===========================  =======  ==========================================

The first three are the only colours that occur *inside* a region, so cutting
everything that is not the content colour removes the adverts and nothing else.

Indentation must not be used for this: an answer option is an indented row with a
radio button starting at x 118, and its wrapped continuation starts at x 156 -
exactly where advert text also starts. An earlier left-edge rule looked clean
because the x distribution has an empty 105..115 gap, but it silently dropped
every A/B/C/D option, which is why the colour test replaced it.

Regions are built so that vertical space is only ever discarded when it contains
no content at all: a gap between two content elements is cut when a furniture
element sits inside it, and leading or trailing furniture is clipped away.
Embedded images count as content, so question diagrams survive.
"""

from __future__ import annotations

import re

from api.modules.ingest.domain import Band, Marker, Region
from api.modules.ingest.internal._layout import TextLine, extract_glyphs, group_lines
from api.modules.ingest.internal._pdf_objects import PdfPage

#: Fill colour of every piece of question and answer text.
CONTENT_COLOR = (0.267, 0.251, 0.235)

#: Allowed per-channel deviation from :data:`CONTENT_COLOR`. Colours are read
#: straight from the ``rg`` operator and rounded to three decimals, so they are
#: exact; the tolerance only guards against rounding at a different precision.
_COLOR_TOLERANCE = 0.01

#: Content images are always placed flush with the content column at x ~ 85.9.
#: The site logo (x 37.6), the favicon (x 719.7) and the footer badge (x 215.7)
#: fall outside this range and are correctly treated as furniture.
_IMAGE_X_RANGE = (70.0, 110.0)

#: Images shorter than this are rules or spacers, not diagrams.
_MIN_IMAGE_HEIGHT = 10.0

#: Printable area of a page in stream units, taken from the page frame the
#: producer draws at 37.9 .. 1019.5.
PAGE_CONTENT_TOP = 36.0
PAGE_CONTENT_BOTTOM = 1021.0

#: Horizontal crop window: content text spans x 86..730 and full-width diagrams
#: are placed at x 85.9 with a width of 645.9, so 731.8 is the right-hand edge.
CONTENT_X0 = 84.0
CONTENT_X1 = 734.0

#: Vertical padding kept around a band so glyphs are not clipped, in stream units.
_BAND_PADDING = 5.0

_BLOCK = re.compile(r"\nq\n(.*?)\nQ", re.S)
_CM = re.compile(
    r"(-?[\d.]+) (-?[\d.]+) (-?[\d.]+) (-?[\d.]+) (-?[\d.]+) (-?[\d.]+) cm"
)
_PAINT_IMAGE = re.compile(r"/(Image\d+)\s+Do")


def is_content_line(line: TextLine) -> bool:
    """
    Return whether a line belongs to the question rather than to the site.

    Args:
        line: Line to classify.

    Returns:
        ``True`` when the line's dominant fill colour is :data:`CONTENT_COLOR`.
    """
    if line.color is None:
        return False

    return all(
        abs(channel - reference) <= _COLOR_TOLERANCE
        for channel, reference in zip(line.color, CONTENT_COLOR)
    )


def image_rectangles(page: PdfPage) -> list[tuple[float, float, float, float]]:
    """
    Find where images are painted on a page.

    An image is drawn by mapping the unit square through the block's ``cm``
    matrix, so the placement rectangle comes from transforming that square.

    Args:
        page: Page whose content stream has been inflated.

    Returns:
        ``(y_from, y_to, x_from, x_to)`` rectangles in stream units, for every
        painted image regardless of whether it is content.

    Note:
        Tall diagrams are painted once per page they cross, with the rectangle
        extending outside the page, so coordinates may be negative or exceed the
        page height. Callers must clamp to the printable area.
    """
    rectangles: list[tuple[float, float, float, float]] = []

    for block in _BLOCK.findall(page.content):
        if not _PAINT_IMAGE.search(block):
            continue
        matrix = _CM.search(block)
        if not matrix:
            continue

        a, b, c, d, e, f = (float(matrix.group(i)) for i in range(1, 7))
        corners = [(e, f), (a + e, b + f), (c + e, d + f), (a + c + e, b + d + f)]
        xs = [x for x, _ in corners]
        ys = [y for _, y in corners]
        rectangles.append((min(ys), max(ys), min(xs), max(xs)))

    return rectangles


def classify_page(
    page: PdfPage,
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """
    Split everything drawn on a page into content and furniture extents.

    Args:
        page: Page to inspect.

    Returns:
        ``(content, furniture)``, each a sorted list of ``(y_from, y_to)``
        intervals in stream units. Content covers question text and diagrams;
        furniture covers adverts, navigation, comments and the footer.
    """
    content: list[tuple[float, float]] = []
    furniture: list[tuple[float, float]] = []

    for line in group_lines(extract_glyphs(page)):
        target = content if is_content_line(line) else furniture
        target.append((line.top, line.bottom))

    for y_from, y_to, x_from, _x_to in image_rectangles(page):
        if _IMAGE_X_RANGE[0] <= x_from < _IMAGE_X_RANGE[1]:
            if y_to - y_from >= _MIN_IMAGE_HEIGHT:
                content.append(
                    (max(y_from, PAGE_CONTENT_TOP), min(y_to, PAGE_CONTENT_BOTTOM))
                )
        else:
            furniture.append((y_from, y_to))

    return (
        sorted(i for i in content if i[1] > i[0]),
        sorted(i for i in furniture if i[1] > i[0]),
    )


class PageIndex:
    """
    Cached content and furniture extents for the pages of one source file.

    Layout analysis is the expensive part of the build, so each page is analysed
    once and reused by every region that touches it.
    """

    def __init__(self, pages: list[PdfPage]):
        """
        Analyse every page of a file.

        Args:
            pages: Pages of one source PDF, in document order.
        """
        self.pages = pages
        self.content: dict[int, list[tuple[float, float]]] = {}
        self.furniture: dict[int, list[tuple[float, float]]] = {}

        for page in pages:
            content, furniture = classify_page(page)
            self.content[page.index] = content
            self.furniture[page.index] = furniture


def _bands_for_page(
    index: PageIndex,
    page_index: int,
    y_from: float,
    y_to: float,
) -> list[Band]:
    """
    Build the bands of one page inside a vertical window.

    Content elements inside the window are grouped into runs; a run is broken
    wherever a furniture element sits between two consecutive content elements.
    Space before the first and after the last content element is dropped, so
    leading and trailing furniture never reaches the image.

    Args:
        index: Analysed pages of the file.
        page_index: Page to slice.
        y_from: Upper bound of the window, in stream units.
        y_to: Lower bound of the window, in stream units.

    Returns:
        Bands in top-to-bottom order; empty when the window holds no content.
    """
    window = [
        (max(top, y_from), min(bottom, y_to))
        for top, bottom in index.content.get(page_index, [])
        if bottom > y_from and top < y_to
    ]
    window = [(top, bottom) for top, bottom in window if bottom > top]
    if not window:
        return []

    furniture = [
        (top, bottom)
        for top, bottom in index.furniture.get(page_index, [])
        if bottom > y_from and top < y_to
    ]

    runs: list[list[tuple[float, float]]] = [[window[0]]]
    for previous, current in zip(window, window[1:]):
        gap_start, gap_end = previous[1], current[0]
        interrupted = gap_end > gap_start and any(
            top < gap_end and gap_start < bottom for top, bottom in furniture
        )
        if interrupted:
            runs.append([current])
        else:
            runs[-1].append(current)

    bands = []
    for run in runs:
        top = max(min(item[0] for item in run) - _BAND_PADDING, y_from)
        bottom = min(max(item[1] for item in run) + _BAND_PADDING, y_to)
        if bottom > top:
            bands.append(Band(page_index=page_index, y_from=top, y_to=bottom))

    return bands


def build_region(
    index: PageIndex,
    start: Marker,
    end: Marker | None,
) -> Region:
    """
    Turn a pair of markers into the region between them.

    The window runs from the start marker down to just above the next marker,
    continuing across page boundaries when needed - the common case, since a
    question and its answer nearly always sit on different pages.

    Args:
        index: Analysed pages of the file.
        start: Marker opening the region; its own line is included, so the
            rendered image shows the ``Question N of NNN`` or
            ``Correct Answer: X`` heading.
        end: Marker opening the next region, or ``None`` for the last answer in
            a file, which then runs to the end of the document. The comment
            section that follows is furniture and is clipped automatically.

    Returns:
        The region, with site furniture already cut out.
    """
    first_page = start.page_index
    top = start.top - _BAND_PADDING

    if end is not None:
        last_page = end.page_index
        bottom = end.top - _BAND_PADDING
    else:
        last_page = index.pages[-1].index
        bottom = PAGE_CONTENT_BOTTOM

    bands: list[Band] = []
    for page_index in range(first_page, last_page + 1):
        window_top = top if page_index == first_page else PAGE_CONTENT_TOP
        window_bottom = bottom if page_index == last_page else PAGE_CONTENT_BOTTOM
        bands.extend(_bands_for_page(index, page_index, window_top, window_bottom))

    return Region(bands=tuple(bands))


# code-documentation-2026-09-05T18:24:39
