"""
Orchestration of the dataset build: source PDFs in, images and catalog out.

The pipeline per source file is: inflate the pages, classify content against site
furniture, locate the ``Question`` and ``Correct Answer`` markers, pair them into
regions, render each region to a PNG and record it in the question catalog.

Every structural assumption is asserted rather than trusted, because a silent
mis-detection would produce a plausible-looking image with the wrong content:

* markers must strictly alternate question, answer, question, answer ...
* question numbers must be contiguous from 1 across the whole corpus
* the digit count printed in each marker must match its assigned number
* both regions of every question must be non-empty and render to real pixels

The digit-count test is the substitute for reading the printed number: this font
uses tabular digits, so 0, 2..9 are indistinguishable by shape and only the narrow
1 can be told apart. Checking the digit count still pins the numbering, because a
wrong offset would misplace the 9 -> 10 and 99 -> 100 transitions.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pymupdf

from api.modules.ingest.domain import (
    MARKER_ANSWER,
    MARKER_QUESTION,
    ExtractedQuestion,
    Marker,
    Region,
)
from api.modules.ingest.internal._content import PageIndex, build_region
from api.modules.ingest.internal._layout import find_markers
from api.modules.ingest.internal._pdf_objects import load_pages
from api.modules.ingest.internal._render import (
    DEFAULT_DPI,
    MIN_IMAGE_HEIGHT_PX,
    RegionRenderError,
    render_region,
)
from api.modules.questionbank import api as questionbank
from api.modules.shared.api import IMAGES_DIR, SOURCE_DIR, get_connection, get_logger, get_run_id

logger = get_logger(__name__)

#: Source files are named ``az-700_exam_questions_with_answers_NN.pdf``; sorting
#: by name puts the questions in their printed order.
SOURCE_GLOB = "az-700_exam_questions_with_answers_*.pdf"

#: Progress callback signature: ``(files_done, files_total, current_file_name)``.
ProgressCallback = Callable[[int, int, str], None]

#: An answer image no taller than this holds only the ``Correct Answer:`` label.
LABEL_ONLY_HEIGHT_PX = 60

#: Notes attached to questions that deserve a human glance in the report.
NOTE_LETTER_ONLY = "answer letter only, no explanation"
NOTE_ANSWER_AS_IMAGE = "answer is an Answer Area image, not a letter"
NOTE_NO_ANSWER = "no answer at all in the source PDF"


class IngestError(RuntimeError):
    """Raised when a source file breaks the expected question/answer structure."""


@dataclass
class QuestionReport:
    """
    What the build produced for one question, for the verification report.

    Attributes:
        number: Assigned question number.
        source_file: Source PDF name.
        page_span: First and last page index touched, one-based for display.
        question_image: Catalog-relative path of the question image.
        answer_image: Catalog-relative path of the answer image.
        question_bands: How many bands the question region needed.
        answer_bands: How many bands the answer region needed.
        question_height_px: Height of the rendered question image.
        answer_height_px: Height of the rendered answer image.
        notes: Anything unusual worth a human glance.
    """

    number: int
    source_file: str
    page_span: tuple[int, int]
    question_image: str
    answer_image: str
    question_bands: int
    answer_bands: int
    question_height_px: int
    answer_height_px: int
    notes: list[str] = field(default_factory=list)


@dataclass
class IngestReport:
    """
    Outcome of a whole dataset build.

    Attributes:
        run_id: Run identifier that all log lines of this build carry.
        files: Source files processed.
        pages: Pages inflated.
        questions: Questions written to the catalog.
        available: How many of them a session may actually draw - questions whose
            source printed no answer are excluded, because they cannot be
            self-graded.
        images: Image files written.
        dpi: Resolution the images were rendered at.
        duration_seconds: Wall-clock time of the build.
        cancelled: Whether the caller stopped the build early.
        entries: Per-question detail, ordered by question number.
        errors: Structural problems. A non-empty list means the dataset must not
            be trusted.
        warnings: Oddities that do not invalidate the dataset.
    """

    run_id: str
    files: int = 0
    pages: int = 0
    questions: int = 0
    available: int = 0
    images: int = 0
    dpi: int = DEFAULT_DPI
    duration_seconds: float = 0.0
    cancelled: bool = False
    entries: list[QuestionReport] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether the build finished with no structural errors."""
        return not self.errors and not self.cancelled


def _pair_markers(source_name: str, markers: list[Marker]) -> list[tuple[Marker, Marker]]:
    """
    Pair each question marker with the answer marker that follows it.

    Args:
        source_name: File name, used in error messages.
        markers: Markers of one file in reading order.

    Returns:
        ``(question_marker, answer_marker)`` pairs in printed order.

    Raises:
        IngestError: If the markers do not strictly alternate starting with a
            question and ending with an answer.
    """
    if not markers:
        raise IngestError(f"{source_name}: no question markers found")

    expected = MARKER_QUESTION
    for marker in markers:
        if marker.kind != expected:
            raise IngestError(
                f"{source_name}: expected a {expected} marker on page "
                f"{marker.page_index + 1} at y={marker.top:.0f} but found {marker.kind}"
            )
        expected = MARKER_ANSWER if expected == MARKER_QUESTION else MARKER_QUESTION

    if expected != MARKER_QUESTION:
        raise IngestError(f"{source_name}: last question has no answer marker")

    return list(zip(markers[0::2], markers[1::2]))


def _extract_file(
    pdf_path: Path, first_number: int
) -> tuple[list[ExtractedQuestion], int, float]:
    """
    Locate every question in one source file.

    Args:
        pdf_path: Source PDF.
        first_number: Number to assign to this file's first question.

    Returns:
        ``(questions, page_count, scale)`` where scale is the page's
        stream-units-to-points factor, needed later for rendering.

    Raises:
        IngestError: If the file's marker structure is broken, or a marker's
            printed digit count contradicts its assigned number.
    """
    pages = load_pages(pdf_path)
    index = PageIndex(pages)

    markers = [marker for page in pages for marker in find_markers(page)]
    pairs = _pair_markers(pdf_path.name, markers)

    questions: list[ExtractedQuestion] = []
    for offset, (question_marker, answer_marker) in enumerate(pairs):
        number = first_number + offset

        if question_marker.digit_count != len(str(number)):
            raise IngestError(
                f"{pdf_path.name}: marker on page {question_marker.page_index + 1} prints "
                f"{question_marker.digit_count} digits but was assigned number {number}"
            )

        # The answer runs until the next question, or to the end of the file for
        # the last one - the comment section that follows is furniture.
        following = markers[markers.index(answer_marker) + 1 :]
        next_question = following[0] if following else None

        questions.append(
            ExtractedQuestion(
                number=number,
                source_file=pdf_path.name,
                question=build_region(index, question_marker, answer_marker),
                answer=build_region(index, answer_marker, next_question),
                answer_given=answer_marker.trailing_words > 0,
            )
        )

    return questions, len(pages), abs(pages[0].base_matrix[0])


def _geometry(question: ExtractedQuestion, dpi: int) -> dict:
    """
    Build the stored geometry payload for a question.

    Args:
        question: Located question.
        dpi: Resolution its images were rendered at.

    Returns:
        A JSON-serialisable description of both regions, enough to re-render the
        images at another resolution without re-parsing the PDF.
    """
    return {
        "dpi": dpi,
        "question_bands": question.question.to_json(),
        "answer_bands": question.answer.to_json(),
    }


def _render_and_save(
    document: pymupdf.Document,
    region: Region,
    scale: float,
    dpi: int,
    destination: Path,
) -> int:
    """
    Render a region and write it as a PNG.

    Args:
        document: Open source document.
        region: Region to render.
        scale: Stream-units-to-points factor of the pages.
        dpi: Target resolution.
        destination: File to write.

    Returns:
        Height of the written image in pixels.

    Raises:
        RegionRenderError: If the region renders to nothing usable.
    """
    image = render_region(document, region, scale, dpi=dpi)
    if image.height < MIN_IMAGE_HEIGHT_PX:
        raise RegionRenderError(
            f"rendered only {image.height}px, below the {MIN_IMAGE_HEIGHT_PX}px minimum"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG")
    return image.height


def build_dataset(
    source_dir: Path | None = None,
    dpi: int = DEFAULT_DPI,
    limit: int | None = None,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> IngestReport:
    """
    Extract every question from the source PDFs into images and the catalog.

    Rebuilding is idempotent: images are overwritten and catalog rows are
    upserted on the question number, so a repeated run leaves the same dataset.

    Args:
        source_dir: Directory holding the source PDFs. Defaults to the project's
            ``source`` directory.
        dpi: Rendering resolution.
        limit: Process only the first N files. For quick checks during
            development; a limited run is reported as incomplete.
        progress: Called after each file with ``(done, total, file_name)``.
        cancel: Checked between files; when set, the build stops and the report
            is marked cancelled.

    Returns:
        An :class:`IngestReport` describing what was produced and every problem
        found. Inspect :attr:`IngestReport.ok` before trusting the dataset.
    """
    started = time.perf_counter()
    directory = source_dir or SOURCE_DIR
    report = IngestReport(run_id=get_run_id(), dpi=dpi)

    files = sorted(directory.glob(SOURCE_GLOB))
    if limit is not None:
        files = files[:limit]
    if not files:
        report.errors.append(f"no source files matching {SOURCE_GLOB} in {directory}")
        return report

    logger.info(
        "dataset build started: %d source files from %s at %d dpi",
        len(files),
        directory,
        dpi,
    )

    connection = get_connection()
    questionbank.ensure_schema(connection)

    next_number = 1
    try:
        for position, pdf_path in enumerate(files, start=1):
            if cancel is not None and cancel.is_set():
                report.cancelled = True
                logger.warning("dataset build cancelled before %s", pdf_path.name)
                break

            try:
                questions, page_count, scale = _extract_file(pdf_path, next_number)
            except (IngestError, OSError) as error:
                report.errors.append(str(error))
                logger.error("%s: extraction failed: %s", pdf_path.name, error)
                continue

            report.files += 1
            report.pages += page_count

            document = pymupdf.open(pdf_path)
            try:
                for question in questions:
                    entry = _store_question(
                        document, connection, question, scale, dpi, report
                    )
                    if entry is not None:
                        report.entries.append(entry)
            finally:
                document.close()

            next_number += len(questions)
            connection.commit()

            logger.info(
                "%s: %d pages, %d questions (numbers %d-%d)",
                pdf_path.name,
                page_count,
                len(questions),
                questions[0].number if questions else 0,
                questions[-1].number if questions else 0,
            )
            if progress is not None:
                progress(position, len(files), pdf_path.name)

        report.available = questionbank.count_available(connection)
    finally:
        connection.commit()
        connection.close()

    report.questions = len(report.entries)
    report.duration_seconds = time.perf_counter() - started

    if limit is None and not report.cancelled:
        _verify_numbering(report)

    as_image = [e.number for e in report.entries if NOTE_ANSWER_AS_IMAGE in e.notes]
    if as_image:
        report.warnings.append(
            f"{len(as_image)} answers are given as a marked-up Answer Area image "
            f"rather than a letter - these are fine, just not text"
        )

    unanswered = [e.number for e in report.entries if NOTE_NO_ANSWER in e.notes]
    if unanswered:
        report.warnings.append(
            f"{len(unanswered)} questions have no answer at all in the source PDF: "
            f"{unanswered}"
        )

    logger.info(
        "dataset build finished: %d files, %d pages, %d questions "
        "(%d available for an exam), %d images in %.1fs (errors=%d, warnings=%d)",
        report.files,
        report.pages,
        report.questions,
        report.available,
        report.images,
        report.duration_seconds,
        len(report.errors),
        len(report.warnings),
    )
    return report


def _store_question(
    document: pymupdf.Document,
    connection: sqlite3.Connection,
    question: ExtractedQuestion,
    scale: float,
    dpi: int,
    report: IngestReport,
) -> QuestionReport | None:
    """
    Render one question's images and record it in the catalog.

    Args:
        document: Open source document.
        connection: Open database connection.
        question: Located question.
        scale: Stream-units-to-points factor.
        dpi: Rendering resolution.
        report: Report to append errors and warnings to.

    Returns:
        The report entry, or ``None`` if the question could not be rendered.
    """
    stem = f"q{question.number:04d}"
    question_name = f"{stem}_question.png"
    answer_name = f"{stem}_answer.png"

    if not question.question.bands:
        report.errors.append(f"question {question.number}: question region is empty")
        return None
    if not question.answer.bands:
        report.errors.append(f"question {question.number}: answer region is empty")
        return None

    try:
        question_height = _render_and_save(
            document, question.question, scale, dpi, IMAGES_DIR / question_name
        )
        answer_height = _render_and_save(
            document, question.answer, scale, dpi, IMAGES_DIR / answer_name
        )
    except RegionRenderError as error:
        report.errors.append(f"question {question.number}: {error}")
        logger.error("question %d: render failed: %s", question.number, error)
        return None

    report.images += 2

    notes: list[str] = []
    if len(question.question.bands) > 1 or len(question.answer.bands) > 1:
        notes.append("spans pages or has furniture cut out")

    # An answer can be a letter, a marked-up Answer Area image, or - for part of
    # this material - genuinely absent. Only the last case is a defect, and it is
    # recognised by the region holding nothing but the label line itself. Such a
    # question cannot be self-graded, so it is recorded as having no answer and
    # is never drawn for a session.
    label_only = answer_height < LABEL_ONLY_HEIGHT_PX
    has_answer = True
    if question.answer_given:
        if label_only:
            notes.append(NOTE_LETTER_ONLY)
    elif label_only:
        notes.append(NOTE_NO_ANSWER)
        has_answer = False
    else:
        notes.append(NOTE_ANSWER_AS_IMAGE)

    start_page, end_page = question.page_span
    questionbank.upsert_question(
        connection,
        questionbank.Question(
            id=question.number,
            source_file=question.source_file,
            start_page=start_page,
            end_page=end_page,
            question_image=f"images/{question_name}",
            answer_image=f"images/{answer_name}",
            geometry=_geometry(question, dpi),
            has_answer=has_answer,
        ),
    )

    return QuestionReport(
        number=question.number,
        source_file=question.source_file,
        page_span=(start_page + 1, end_page + 1),
        question_image=f"images/{question_name}",
        answer_image=f"images/{answer_name}",
        question_bands=len(question.question.bands),
        answer_bands=len(question.answer.bands),
        question_height_px=question_height,
        answer_height_px=answer_height,
        notes=notes,
    )


def _verify_numbering(report: IngestReport) -> None:
    """
    Check that the extracted question numbers form a gapless sequence from 1.

    Args:
        report: Report to append errors to.
    """
    numbers = sorted(entry.number for entry in report.entries)
    if not numbers:
        report.errors.append("no questions were extracted")
        return

    duplicates = {n for n in numbers if numbers.count(n) > 1}
    if duplicates:
        report.errors.append(f"duplicate question numbers: {sorted(duplicates)}")

    expected = list(range(1, len(numbers) + 1))
    if numbers != expected:
        missing = sorted(set(expected) - set(numbers))
        report.errors.append(
            f"question numbers are not contiguous from 1; missing {missing[:10]}"
        )


# code-documentation-2026-09-05T18:24:39
