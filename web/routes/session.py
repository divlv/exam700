"""
Starting a new exam session.

The only choice offered - on the desktop and here - is how many questions to
draw: no topic filters, no difficulty, no "mistakes only" mode. Questions are
always drawn uniformly at random from the available pool.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from api.modules.examsession import api as examsession
from api.modules.questionbank import api as questionbank
from web.deps import get_db, require_login
from web.templating import templates

router = APIRouter()

#: Same preset sizes the desktop new-session screen offers.
PRESETS = (10, 20, 30, 50, 100)


def _render_form(request: Request, conn: sqlite3.Connection, error: str | None = None):
    available = questionbank.count_available(conn)
    presets = [size for size in PRESETS if size <= available]
    default = min(examsession.get_default_question_count(conn), max(available, 1))

    return templates.TemplateResponse(
        request,
        "new_session.html",
        {
            "available": available,
            "presets": presets,
            "show_all_option": available > 0 and available not in presets,
            "default": default,
            "error": error,
        },
    )


@router.get("/session/new")
def new_session_form(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
):
    return _render_form(request, conn)


@router.post("/session/new")
def new_session_submit(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
    count: str = Form(...),
    custom_count: str = Form(""),
):
    available = questionbank.count_available(conn)
    raw = custom_count if count == "custom" else count
    try:
        size = int(raw)
    except ValueError:
        size = 0
    # Clamp exactly as the desktop screen's spinbox does, rather than reject
    # an out-of-range value outright: a phone number input can be nudged past
    # its own min/max by hand, and the request is a plain form post either way.
    size = max(1, min(size, max(available, 1)))

    try:
        examsession.start_session(conn, size)
    except examsession.NoQuestionsAvailable:
        return _render_form(request, conn, error="Недостаточно доступных вопросов.")

    examsession.set_default_question_count(conn, size)
    return RedirectResponse(url="/exam", status_code=303)
