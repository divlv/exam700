"""
Administration: reset statistics, manage badly-cropped flags, and view the
questions the source PDFs never printed an answer for.

Three tabs, matching the desktop screen's notebook exactly.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from api.modules.examsession import api as examsession
from api.modules.questionbank import api as questionbank
from web.deps import get_db, require_login
from web.templating import templates
from web.urls import image_url

router = APIRouter()

_TABS = ("stats", "broken", "unanswered")


def _source_label(source_file: str) -> str:
    """``"az-700_exam_questions_with_answers_07.pdf"`` -> ``"07"``."""
    return source_file.removeprefix("az-700_exam_questions_with_answers_").removesuffix(".pdf")


@router.get("/admin")
def admin(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
    tab: str = "stats",
):
    if tab not in _TABS:
        tab = "stats"

    context: dict[str, object] = {"tab": tab}
    if tab == "stats":
        context["sessions"] = examsession.list_sessions(conn)
    elif tab == "broken":
        context["broken"] = [
            {
                "mark": mark,
                "question_image_url": image_url(mark.question.question_image),
                "answer_image_url": image_url(mark.question.answer_image),
                "source_label": _source_label(mark.question.source_file),
            }
            for mark in questionbank.list_broken(conn)
        ]
    else:
        context["unanswered"] = questionbank.list_unanswered(conn)

    return templates.TemplateResponse(request, "admin.html", context)


@router.post("/admin/sessions/{session_id}/reset")
def reset_one_session(
    session_id: int,
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
):
    examsession.reset_session(conn, session_id)
    return RedirectResponse(url="/admin?tab=stats", status_code=303)


@router.post("/admin/sessions/reset-all")
def reset_all_sessions_route(
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
):
    examsession.reset_all_sessions(conn)
    return RedirectResponse(url="/admin?tab=stats", status_code=303)


@router.post("/admin/broken/{question_id}/unmark")
def unmark_broken_route(
    question_id: int,
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
):
    questionbank.unmark_broken(conn, question_id)
    return RedirectResponse(url="/admin?tab=broken", status_code=303)


@router.post("/admin/broken/clear")
def clear_broken_route(
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
):
    questionbank.clear_broken(conn)
    return RedirectResponse(url="/admin?tab=broken", status_code=303)
