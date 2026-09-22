"""
Minimal PDF reader for the source exam files.

The source PDFs (produced by "Microsoft: Print To PDF") carry **no text layer**:
they contain zero ``/Font`` references and zero ``BT``/``Tj``/``TJ`` operators, and
every glyph is drawn as a filled vector outline. Text extraction is therefore
impossible and a full PDF library is not needed for layout analysis - the page
structure is recovered from the content streams directly.

This module reads just enough of the file structure for that: locate the indirect
objects, walk the page tree in order, and inflate each page's content streams.
Rasterization is a separate concern and lives in :mod:`_render`.

These files are simple by construction: uncompressed object headers, no object
streams, no cross-reference streams, no encryption, and a single ``/Kids`` array.
Anything outside that shape raises :class:`PdfStructureError` rather than being
silently mis-parsed.

Example:
    >>> pages = load_pages(Path("source/az-700_exam_questions_with_answers_01.pdf"))
    >>> len(pages)
    7
    >>> pages[0].width_pt, pages[0].height_pt
    (612.0, 792.0)
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass
from pathlib import Path

#: Indirect object header, e.g. ``12 0 obj``. Generation numbers are always 0 here.
_OBJ_HEADER = re.compile(rb"(?:^|[\r\n])(\d+)\s+0\s+obj\b")

#: ``/Contents`` as an array of references, or as a single reference.
_CONTENTS_ARRAY = re.compile(rb"/Contents\s*\[([^\]]*)\]")
_CONTENTS_SINGLE = re.compile(rb"/Contents\s+(\d+)\s+0\s+R")

_REFERENCE = re.compile(rb"(\d+)\s+0\s+R")
_KIDS = re.compile(rb"/Kids\s*\[([^\]]*)\]")
_MEDIABOX = re.compile(
    rb"/MediaBox\s*\[\s*([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)\s+([\d.+-]+)\s*\]"
)
_STREAM_START = re.compile(rb"stream\r?\n")

#: Leading transform of a content stream, e.g. ``0.75 0 0 -0.75 0 792 cm``.
_LEADING_CM = re.compile(
    r"\s*(-?[\d.]+) (-?[\d.]+) (-?[\d.]+) (-?[\d.]+) (-?[\d.]+) (-?[\d.]+) cm"
)


class PdfStructureError(RuntimeError):
    """Raised when a source PDF does not match the expected simple structure."""


@dataclass(frozen=True)
class PdfPage:
    """
    One page of a source PDF with its content stream already inflated.

    Coordinates inside ``content`` are *stream* coordinates, not PDF points: the
    content stream opens with a flip-and-scale transform (``0.75 0 0 -0.75 0 792
    cm``) so that stream y grows downwards from the top of the page. Use
    :meth:`to_top_left_pt` to convert.

    Attributes:
        index: Zero-based page index within the file.
        width_pt: Page width in PDF points (1/72 inch).
        height_pt: Page height in PDF points.
        content: All of the page's content streams, inflated and concatenated in
            order, decoded as latin-1 so byte offsets stay one-to-one with
            characters.
        base_matrix: The leading ``cm`` transform of the content stream.
    """

    index: int
    width_pt: float
    height_pt: float
    content: str
    base_matrix: tuple[float, float, float, float, float, float]

    def to_top_left_pt(self, x_stream: float, y_stream: float) -> tuple[float, float]:
        """
        Convert stream coordinates to points measured from the page's top-left.

        Args:
            x_stream: X coordinate as it appears in the content stream.
            y_stream: Y coordinate as it appears in the content stream.

        Returns:
            ``(x_pt, y_pt)`` where both are in PDF points and y grows downwards
            from the top edge - the orientation image cropping needs.
        """
        a, b, c, d, e, f = self.base_matrix
        x_user = a * x_stream + c * y_stream + e
        y_user = b * x_stream + d * y_stream + f
        return x_user, self.height_pt - y_user

    @property
    def stream_height(self) -> float:
        """Page height expressed in stream units (``height_pt`` / scale)."""
        scale = abs(self.base_matrix[3]) or 1.0
        return self.height_pt / scale


def _inflate(data: bytes, obj_start: int) -> str:
    """
    Inflate the stream belonging to the object starting at ``obj_start``.

    Args:
        data: Whole file contents.
        obj_start: Offset just past the object's ``N 0 obj`` header.

    Returns:
        The inflated stream decoded as latin-1, or an empty string if the object
        holds no stream or cannot be inflated (both are tolerated: a page may
        legitimately have an empty resource object).
    """
    obj_end = data.find(b"endobj", obj_start)
    segment = data[obj_start:obj_end]

    match = _STREAM_START.search(segment)
    if not match:
        return ""

    start = obj_start + match.end()
    end = data.find(b"endstream", start)
    if end == -1:
        return ""

    try:
        return zlib.decompress(data[start:end]).decode("latin-1")
    except zlib.error:
        return ""


def load_pages(pdf_path: Path) -> list[PdfPage]:
    """
    Read a source PDF and return its pages in document order.

    Args:
        pdf_path: Path to one of the ``az-700_exam_questions_with_answers_NN.pdf``
            files.

    Returns:
        One :class:`PdfPage` per page, ordered as in the ``/Kids`` array.

    Raises:
        PdfStructureError: If the file is encrypted, has no page tree, or a page
            lacks a ``/Contents`` entry, a ``/MediaBox`` or the expected leading
            transform - i.e. whenever the simplifying assumptions do not hold.
    """
    data = pdf_path.read_bytes()

    if b"/Encrypt" in data:
        raise PdfStructureError(f"{pdf_path.name}: encrypted PDFs are not supported")

    offsets = {int(m.group(1)): m.end() for m in _OBJ_HEADER.finditer(data)}

    kids_match = _KIDS.search(data)
    if not kids_match:
        raise PdfStructureError(f"{pdf_path.name}: no /Kids page tree found")
    kids = [int(ref) for ref in _REFERENCE.findall(kids_match.group(1))]

    pages: list[PdfPage] = []
    for index, obj_num in enumerate(kids):
        if obj_num not in offsets:
            raise PdfStructureError(f"{pdf_path.name}: page object {obj_num} is missing")

        page_start = offsets[obj_num]
        page_dict = data[page_start : data.find(b"endobj", page_start)]

        array = _CONTENTS_ARRAY.search(page_dict)
        if array:
            content_refs = [int(ref) for ref in _REFERENCE.findall(array.group(1))]
        else:
            single = _CONTENTS_SINGLE.search(page_dict)
            if not single:
                raise PdfStructureError(
                    f"{pdf_path.name}: page {index} has no /Contents entry"
                )
            content_refs = [int(single.group(1))]

        content = "\n".join(
            _inflate(data, offsets[ref]) for ref in content_refs if ref in offsets
        )

        box = _MEDIABOX.search(page_dict)
        if not box:
            raise PdfStructureError(f"{pdf_path.name}: page {index} has no /MediaBox")
        x0, y0, x1, y1 = (float(box.group(i)) for i in range(1, 5))

        leading = _LEADING_CM.match(content)
        if not leading:
            raise PdfStructureError(
                f"{pdf_path.name}: page {index} does not start with a cm transform"
            )
        matrix = tuple(float(leading.group(i)) for i in range(1, 7))

        pages.append(
            PdfPage(
                index=index,
                width_pt=abs(x1 - x0),
                height_pt=abs(y1 - y0),
                content=content,
                base_matrix=matrix,  # type: ignore[arg-type]
            )
        )

    if not pages:
        raise PdfStructureError(f"{pdf_path.name}: page tree is empty")

    return pages


# code-documentation-2026-09-05T18:24:39
