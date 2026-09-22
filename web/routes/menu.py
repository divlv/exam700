"""Main menu: catalog stats and the entry point to every other screen."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request

from api.modules.examsession import api as examsession
from api.modules.questionbank import api as questionbank
from web.deps import get_db, require_login
from web.templating import templates

router = APIRouter()


@router.get("/")
def menu(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
):
    total = questionbank.count(conn)
    if total == 0:
        return templates.TemplateResponse(request, "menu.html", {"total": 0})

    available = questionbank.count_available(conn)
    unanswered = len(questionbank.list_unanswered(conn))
    # The three sets are disjoint by construction (a question can only be
    # flagged once it has been drawn, and only answered questions are ever
    # drawn), so this subtraction is exact - the same arithmetic the desktop
    # main menu uses.
    flagged = total - available - unanswered

    return templates.TemplateResponse(
        request,
        "menu.html",
        {
            "total": total,
            "available": available,
            "flagged": flagged,
            "overview": examsession.get_overview(conn),
            "active_session": examsession.get_active_session(conn),
        },
    )
