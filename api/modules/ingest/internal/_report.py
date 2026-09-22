"""
The verification report: a contact sheet of every extracted question.

Automated checks can prove the structure is consistent, but only a human can
confirm that a crop actually shows the right question with all of its options.
This writes a single self-contained HTML page next to the images, showing each
question image beside its answer image, so the whole dataset can be scanned in a
few minutes.

Rows that deserve a closer look - regions that cross a page boundary, regions
that had adverts cut out of them, answers with no explanation - are flagged, and
the page can be filtered down to just those.
"""

from __future__ import annotations

import html
from pathlib import Path

#: Report lives in ``data/report`` and the images in ``data/images``, so image
#: sources are addressed one level up.
_IMAGE_PREFIX = ".."

_STYLE = """
:root { color-scheme: light dark; }
body { font: 14px/1.5 -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; padding: 24px;
       background: #fbfbfa; color: #1f1d1b; }
h1 { font-size: 20px; margin: 0 0 4px; }
.sub { color: #6b665f; margin-bottom: 20px; }
.summary { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 18px; }
.stat { background: #fff; border: 1px solid #e6e3de; border-radius: 8px; padding: 10px 14px; }
.stat b { display: block; font-size: 20px; }
.stat span { color: #6b665f; font-size: 12px; }
.bad { border-color: #d9534f; }
.bad b { color: #d9534f; }
.problems { background: #fff3f2; border: 1px solid #d9534f; border-radius: 8px;
            padding: 12px 16px; margin-bottom: 18px; }
.problems li { font-family: ui-monospace, Consolas, monospace; font-size: 12px; }
.controls { margin-bottom: 16px; }
.controls label { margin-right: 16px; }
.row { background: #fff; border: 1px solid #e6e3de; border-radius: 10px;
       padding: 14px 16px; margin-bottom: 14px; }
.row.flagged { border-color: #c9a227; }
.head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap;
        margin-bottom: 10px; }
.num { font-size: 16px; font-weight: 700; }
.meta { color: #6b665f; font-size: 12px; }
.note { background: #fdf6e3; border: 1px solid #e8d9a8; border-radius: 5px;
        padding: 1px 7px; font-size: 12px; }
.pair { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
@media (max-width: 1100px) { .pair { grid-template-columns: 1fr; } }
.cell { min-width: 0; }
.cell h3 { font-size: 12px; text-transform: uppercase; letter-spacing: .04em;
           color: #6b665f; margin: 0 0 6px; }
.frame { border: 1px solid #eceae5; border-radius: 6px; max-height: 460px;
         overflow: auto; background: #fff; }
.frame img { display: block; width: 100%; height: auto; }
@media (prefers-color-scheme: dark) {
  body { background: #171614; color: #eceae5; }
  .stat, .row, .frame { background: #201f1c; border-color: #35322d; }
  .sub, .meta, .stat span, .cell h3 { color: #a5a097; }
  .problems { background: #2e1b1a; }
  .note { background: #2c2617; border-color: #4a4127; }
}
"""

_SCRIPT = """
function applyFilters() {
  const flaggedOnly = document.getElementById('flagged').checked;
  document.querySelectorAll('.row').forEach(function (row) {
    row.hidden = flaggedOnly && !row.classList.contains('flagged');
  });
}
document.getElementById('flagged').addEventListener('change', applyFilters);
"""


def _stat(value: object, label: str, bad: bool = False) -> str:
    """Render one summary tile."""
    css = "stat bad" if bad else "stat"
    return f'<div class="{css}"><b>{html.escape(str(value))}</b><span>{html.escape(label)}</span></div>'


def write_report(report, destination: Path) -> Path:
    """
    Write the contact sheet for a completed build.

    Args:
        report: The :class:`~api.modules.ingest.services.IngestReport` to render.
        destination: Directory to write ``index.html`` into.

    Returns:
        Path of the written HTML file.
    """
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / "index.html"

    flagged = sum(1 for entry in report.entries if entry.notes)
    parts: list[str] = [
        "<title>AZ-700 dataset verification</title>",
        f"<style>{_STYLE}</style>",
        "<h1>AZ-700 dataset verification</h1>",
        f'<div class="sub">Run <code>{html.escape(report.run_id)}</code> &middot; '
        f"{report.dpi} dpi &middot; {report.duration_seconds:.0f}s</div>",
        '<div class="summary">',
        _stat(report.files, "source files"),
        _stat(report.pages, "pages"),
        _stat(report.questions, "questions"),
        _stat(report.available, "usable in an exam"),
        _stat(report.images, "images"),
        _stat(flagged, "need a look"),
        _stat(len(report.errors), "errors", bad=bool(report.errors)),
        "</div>",
    ]

    if report.errors or report.warnings:
        parts.append('<div class="problems"><strong>Problems</strong><ul>')
        for message in report.errors + report.warnings:
            parts.append(f"<li>{html.escape(message)}</li>")
        parts.append("</ul></div>")

    parts.append(
        '<div class="controls"><label>'
        '<input type="checkbox" id="flagged"> show only questions that need a look'
        "</label></div>"
    )

    for entry in report.entries:
        classes = "row flagged" if entry.notes else "row"
        notes = "".join(
            f'<span class="note">{html.escape(note)}</span>' for note in entry.notes
        )
        parts.append(
            f'<div class="{classes}">'
            f'<div class="head">'
            f'<span class="num">Question {entry.number}</span>'
            f'<span class="meta">{html.escape(entry.source_file)} &middot; '
            f"pages {entry.page_span[0]}-{entry.page_span[1]} &middot; "
            f"bands {entry.question_bands}/{entry.answer_bands} &middot; "
            f"{entry.question_height_px}px / {entry.answer_height_px}px</span>"
            f"{notes}</div>"
            f'<div class="pair">'
            f'<div class="cell"><h3>Question</h3><div class="frame">'
            f'<img loading="lazy" src="{_IMAGE_PREFIX}/{html.escape(entry.question_image)}"></div></div>'
            f'<div class="cell"><h3>Correct answer</h3><div class="frame">'
            f'<img loading="lazy" src="{_IMAGE_PREFIX}/{html.escape(entry.answer_image)}"></div></div>'
            f"</div></div>"
        )

    parts.append(f"<script>{_SCRIPT}</script>")

    target.write_text("\n".join(parts), encoding="utf-8")
    return target


# code-documentation-2026-09-05T18:24:39
