"""
Public API of the ``ingest`` module: build the exam dataset from the source PDFs.

This is a build-time module. The exam application does not use it at run time -
it reads the prepared images through ``questionbank`` instead.

Example:
    >>> from api.modules.ingest import api as ingest
    >>> report = ingest.build_dataset()
    >>> report.ok
    True
    >>> ingest.write_report(report)
"""

from __future__ import annotations

from pathlib import Path

from api.modules.ingest.domain import Band, ExtractedQuestion, Marker, Region
from api.modules.ingest.internal._render import DEFAULT_DPI
from api.modules.ingest.internal._report import write_report as _write_report
from api.modules.ingest.services import (
    IngestError,
    IngestReport,
    QuestionReport,
    build_dataset,
)
from api.modules.shared.api import REPORT_DIR

__all__ = [
    "Band",
    "DEFAULT_DPI",
    "ExtractedQuestion",
    "IngestError",
    "IngestReport",
    "Marker",
    "QuestionReport",
    "Region",
    "build_dataset",
    "write_report",
]


def write_report(report: IngestReport, destination: Path | None = None) -> Path:
    """
    Write the human verification report for a build.

    Args:
        report: Report returned by :func:`build_dataset`.
        destination: Directory for ``index.html``. Defaults to the project's
            ``data/report`` directory.

    Returns:
        Path of the written HTML file.
    """
    return _write_report(report, destination or REPORT_DIR)
