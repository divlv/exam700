"""Settings: default session size, auto-reveal and how questions are selected."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from api.modules.examsession import api as examsession
from api.modules.questionbank import api as questionbank
from web.deps import get_db, require_login
from web.templating import templates

router = APIRouter()


@router.get("/settings")
def settings_form(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
    saved: int = 0,
    redrawn: int = 0,
):
    available = questionbank.count_available(conn)
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "default_count": min(
                examsession.get_default_question_count(conn), max(available, 1)
            ),
            "available": available,
            "auto_reveal": examsession.get_auto_reveal(conn),
            "selection_algorithm": examsession.get_selection_algorithm(conn),
            "saved": bool(saved),
            "redrawn": redrawn,
        },
    )


@router.post("/settings")
def settings_submit(
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
    default_count: str = Form(...),
    auto_reveal: str | None = Form(None),
    selection_algorithm: str | None = Form(None),
):
    available = max(questionbank.count_available(conn), 1)
    try:
        count = int(default_count)
    except ValueError:
        count = examsession.DEFAULT_QUESTION_COUNT
    count = max(1, min(count, available))

    examsession.set_default_question_count(conn, count)
    examsession.set_auto_reveal(conn, auto_reveal is not None)

    # A form that does not send the algorithm, or sends something unknown, leaves
    # the stored choice as it is rather than resetting it.
    redrawn = 0
    if selection_algorithm in examsession.SELECTION_ALGORITHMS:
        redrawn = examsession.set_selection_algorithm(conn, selection_algorithm)
    return RedirectResponse(url=f"/settings?saved=1&redrawn={redrawn}", status_code=303)
