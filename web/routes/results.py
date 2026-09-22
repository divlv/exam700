"""Session history: the overall summary and per-session review with flagging."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from api.modules.examsession import api as examsession
from api.modules.questionbank import api as questionbank
from web.deps import get_db, require_login
from web.templating import templates
from web.urls import image_url

router = APIRouter()


@router.get("/results")
def results(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
    missing: int = 0,
):
    return templates.TemplateResponse(
        request,
        "results.html",
        {
            "overview": examsession.get_overview(conn),
            "sessions": examsession.list_sessions(conn),
            "missing": bool(missing),
        },
    )


@router.get("/results/{session_id}")
def session_detail(
    session_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
):
    detail = examsession.get_session_detail(conn, session_id)
    if detail is None:
        return RedirectResponse(url="/results?missing=1", status_code=303)

    rows = []
    for answer in detail.answers:
        question = questionbank.get_question(conn, answer.question_id)
        rows.append(
            {
                "answer": answer,
                "question": question,
                "question_image_url": image_url(question.question_image) if question else None,
                "answer_image_url": image_url(question.answer_image) if question else None,
            }
        )

    return templates.TemplateResponse(
        request, "session_detail.html", {"detail": detail, "rows": rows}
    )


@router.post("/results/{session_id}/flag")
def flag_from_history(
    session_id: int,
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
    question_id: int = Form(...),
):
    """Flag a question as badly cropped from the review screen, then return to it."""
    questionbank.mark_broken(conn, question_id)
    return RedirectResponse(url=f"/results/{session_id}", status_code=303)
