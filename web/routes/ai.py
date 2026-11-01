"""
The "AI check" feature: ask a model about a question, show and list its answers.

The popup on the exam and review screens talks to the JSON endpoints here; the
explanation itself is served as a separate page for a sandboxed frame, because
it is HTML written by a model and must never run in the application's own page.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from api.modules.aiassist import api as aiassist
from api.modules.questionbank import api as questionbank
from api.modules.shared import api as shared
from web.deps import get_db, require_login
from web.templating import templates
from web.urls import image_url

logger = shared.get_logger(__name__)

router = APIRouter()

#: Default direction for each sortable column: newest first, lowest question first.
_DEFAULT_DIRECTION = {
    aiassist.SORT_DATE: aiassist.DIRECTION_DESC,
    aiassist.SORT_QUESTION: aiassist.DIRECTION_ASC,
}

#: The explanation is untrusted HTML. Besides the sanitiser it went through and
#: the sandboxed frame it is shown in, this policy lets the page load nothing
#: and run nothing, even if it is opened directly in a tab.
_VIEW_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
        "sandbox; frame-ancestors 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "private, no-store",
}


def _question_picture(conn: sqlite3.Connection, question_id: int) -> Path:
    """Path of the question's PNG, or a 404 if the catalog has no such question."""
    question = questionbank.get_question(conn, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Вопрос не найден")
    return shared.DATA_DIR / question.question_image


def _answer_meta(answer: aiassist.AiAnswer) -> dict[str, Any]:
    return {
        "answer": answer.answer,
        "summary": answer.summary,
        "model": answer.model,
        "effort": answer.effort,
        "created_at": answer.created_at[:16].replace("T", " "),
        "input_tokens": answer.input_tokens,
        "output_tokens": answer.output_tokens,
    }


def _status_payload(status: aiassist.AiStatus) -> dict[str, Any]:
    """The JSON the popup polls: the state and what goes with it."""
    payload: dict[str, Any] = {"state": status.state}
    if status.state == aiassist.STATE_RUNNING:
        payload["elapsed"] = round(status.elapsed_seconds, 1)
    elif status.state == aiassist.STATE_ERROR:
        payload["message"] = status.message
    elif status.state == aiassist.STATE_DONE and status.answer is not None:
        payload["meta"] = _answer_meta(status.answer)
    return payload


@router.post("/ai/{question_id}")
def ask(
    question_id: int,
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
) -> JSONResponse:
    """
    Start checking the question, or join the check already running, or return
    the stored answer. Never sends a second request for a question.
    """
    picture = _question_picture(conn, question_id)
    try:
        status = aiassist.start(question_id, picture)
    except aiassist.AiNotConfigured as error:
        logger.warning("ai requested but not configured: %s", error)
        return JSONResponse(
            {
                "state": "unconfigured",
                "message": "AI не настроен: на сервере нет настроек Azure OpenAI.",
            },
            status_code=503,
        )
    code = 200 if status.state == aiassist.STATE_DONE else 202
    return JSONResponse(_status_payload(status), status_code=code)


@router.get("/ai/{question_id}/status")
def check_status(
    question_id: int,
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
) -> JSONResponse:
    """Where the question's check stands. Polled by the popup while the model works."""
    _question_picture(conn, question_id)
    return JSONResponse(_status_payload(aiassist.status(question_id)))


@router.get("/ai/{question_id}/view", response_class=HTMLResponse)
def view_answer(
    request: Request,
    question_id: int,
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
) -> HTMLResponse:
    """The stored explanation, wrapped in a minimal page for the sandboxed frame."""
    answer = aiassist.get_answer(conn, question_id)
    if answer is None:
        raise HTTPException(status_code=404, detail="Для этого вопроса нет ответа AI")
    response = templates.TemplateResponse(request, "ai_view.html", {"answer": answer})
    response.headers.update(_VIEW_HEADERS)
    return response


@router.get("/ai", response_class=HTMLResponse)
def ai_list(
    request: Request,
    sort: str = Query(aiassist.SORT_DATE),
    direction: str = Query("", alias="dir"),
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
) -> HTMLResponse:
    """Every question sent to the model, with its description and answer."""
    if sort not in _DEFAULT_DIRECTION:
        sort = aiassist.SORT_DATE
    if direction not in (aiassist.DIRECTION_ASC, aiassist.DIRECTION_DESC):
        direction = _DEFAULT_DIRECTION[sort]

    rows = []
    for answer in aiassist.list_answers(conn, sort, direction):
        question = questionbank.get_question(conn, answer.question_id)
        rows.append(
            {
                "answer": answer,
                "created": answer.created_at[:16].replace("T", " "),
                "image_url": image_url(question.question_image) if question else None,
            }
        )

    def header_link(column: str) -> dict[str, str]:
        """Link of a column header: sorts by it, or flips it if it is the current sort."""
        if column == sort:
            flipped = (
                aiassist.DIRECTION_ASC
                if direction == aiassist.DIRECTION_DESC
                else aiassist.DIRECTION_DESC
            )
            arrow = "▲" if direction == aiassist.DIRECTION_ASC else "▼"
            return {"href": f"/ai?sort={column}&dir={flipped}", "arrow": arrow}
        return {"href": f"/ai?sort={column}&dir={_DEFAULT_DIRECTION[column]}", "arrow": ""}

    return templates.TemplateResponse(
        request,
        "ai_list.html",
        {
            "rows": rows,
            "total_tokens": sum(r["answer"].input_tokens + r["answer"].output_tokens for r in rows),
            "sort": sort,
            "direction": direction,
            "date_header": header_link(aiassist.SORT_DATE),
            "question_header": header_link(aiassist.SORT_QUESTION),
        },
    )
