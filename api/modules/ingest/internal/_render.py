"""
Rasterizing regions into the images the exam application displays.

Each band of a region is rendered straight from the PDF with a clip rectangle, so
only the pixels that are needed are produced - rendering whole pages and cropping
afterwards would cost about 11 MB per page at 200 dpi. The bands are then stacked
vertically into a single image per region.

Because the source is vector artwork rather than a scan, rendering is crisp at
any resolution; 200 dpi leaves room for the zoom control in the application while
keeping files small.

A final pass collapses long runs of blank rows. The pages reserve large empty
rectangles for advert frames, and a band may legitimately contain one; leaving
them in would produce images several screens tall with nothing in them.
"""

from __future__ import annotations

import pymupdf
from PIL import Image

from api.modules.ingest.domain import Region
from api.modules.ingest.internal._content import CONTENT_X0, CONTENT_X1

#: Resolution used for the stored images.
DEFAULT_DPI = 200

#: A row counts as blank when every pixel is at least this bright (0-255).
_BLANK_THRESHOLD = 248

#: Blank runs longer than this many pixels are collapsed down to it, so natural
#: paragraph spacing survives but reserved advert space does not.
_MAX_BLANK_RUN_PX = 40

#: Vertical gap inserted between bands that come from different pages, marking
#: the page break instead of joining the text seamlessly.
_PAGE_BREAK_GAP_PX = 12

#: Images shorter than this are treated as a rendering failure.
MIN_IMAGE_HEIGHT_PX = 8


class RegionRenderError(RuntimeError):
    """Raised when a region cannot be turned into an image."""


def _band_image(
    document: pymupdf.Document,
    page_index: int,
    y_from: float,
    y_to: float,
    scale: float,
    dpi: int,
) -> Image.Image | None:
    """
    Render one band of a page.

    Args:
        document: Open source document.
        page_index: Page to render.
        y_from: Band top in stream units.
        y_to: Band bottom in stream units.
        scale: Stream-units-to-points factor of the page (0.75 for these files).
        dpi: Target resolution.

    Returns:
        The rendered band as an RGB image, or ``None`` if the band is degenerate.
    """
    page = document[page_index]
    zoom = dpi / 72.0

    clip = pymupdf.Rect(
        CONTENT_X0 * scale,
        y_from * scale,
        CONTENT_X1 * scale,
        y_to * scale,
    )
    clip = clip & page.rect
    if clip.is_empty or clip.height <= 0:
        return None

    pixmap = page.get_pixmap(
        matrix=pymupdf.Matrix(zoom, zoom), clip=clip, colorspace=pymupdf.csRGB
    )
    if pixmap.width == 0 or pixmap.height == 0:
        return None

    return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def _stack(parts: list[tuple[int, Image.Image]]) -> Image.Image:
    """
    Stack band images into one tall image.

    Args:
        parts: ``(page_index, image)`` pairs in reading order.

    Returns:
        A single image; a thin gap is inserted wherever consecutive bands come
        from different pages.
    """
    width = max(image.width for _, image in parts)

    gaps = [
        _PAGE_BREAK_GAP_PX if previous[0] != current[0] else 0
        for previous, current in zip(parts, parts[1:])
    ]
    height = sum(image.height for _, image in parts) + sum(gaps)

    canvas = Image.new("RGB", (width, height), (255, 255, 255))
    offset = 0
    for position, (_page_index, image) in enumerate(parts):
        canvas.paste(image, (0, offset))
        offset += image.height
        if position < len(gaps):
            offset += gaps[position]

    return canvas


def collapse_blank_runs(
    image: Image.Image,
    max_run: int = _MAX_BLANK_RUN_PX,
    threshold: int = _BLANK_THRESHOLD,
) -> Image.Image:
    """
    Trim blank margins and shorten long blank stretches.

    Args:
        image: Image to tidy.
        max_run: Longest blank run to keep, in pixels.
        threshold: Brightness at or above which a pixel counts as blank.

    Returns:
        A new image with leading and trailing blank rows removed and interior
        blank runs capped at ``max_run``. Returns the input unchanged when it
        contains no ink at all.
    """
    grayscale = image.convert("L")
    width, height = grayscale.size

    # Scanning row bytes with min() keeps the loop in C; a per-pixel Python loop
    # costs seconds on the taller case-study questions.
    raw = grayscale.tobytes()
    blank = [
        min(raw[y * width : (y + 1) * width]) >= threshold for y in range(height)
    ]

    if all(blank):
        return image

    first_ink = blank.index(False)
    last_ink = height - 1 - blank[::-1].index(False)

    keep: list[int] = []
    run = 0
    for y in range(first_ink, last_ink + 1):
        if blank[y]:
            run += 1
            if run <= max_run:
                keep.append(y)
        else:
            run = 0
            keep.append(y)

    if len(keep) == height:
        return image

    # Copy contiguous stretches in one paste each rather than row by row.
    spans: list[tuple[int, int]] = []
    for y in keep:
        if spans and y == spans[-1][1]:
            spans[-1] = (spans[-1][0], y + 1)
        else:
            spans.append((y, y + 1))

    result = Image.new("RGB", (width, len(keep)), (255, 255, 255))
    offset = 0
    for start, stop in spans:
        result.paste(image.crop((0, start, width, stop)), (0, offset))
        offset += stop - start

    return result


def render_region(
    document: pymupdf.Document,
    region: Region,
    scale: float,
    dpi: int = DEFAULT_DPI,
) -> Image.Image:
    """
    Render a whole region into one image.

    Args:
        document: Open source document.
        region: Region to render.
        scale: Stream-units-to-points factor of the pages.
        dpi: Target resolution.

    Returns:
        The region as a single tall RGB image, blank stretches collapsed.

    Raises:
        RegionRenderError: If the region has no bands, or none of them produced
            any pixels - either case means the region was located wrongly and
            must not be stored silently.
    """
    if not region.bands:
        raise RegionRenderError("region has no bands")

    parts: list[tuple[int, Image.Image]] = []
    for band in region.bands:
        image = _band_image(
            document, band.page_index, band.y_from, band.y_to, scale, dpi
        )
        if image is not None:
            parts.append((band.page_index, image))

    if not parts:
        raise RegionRenderError("region produced no pixels")

    return collapse_blank_runs(_stack(parts))


# code-documentation-2026-09-05T18:24:39
