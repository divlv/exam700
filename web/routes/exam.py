"""
The core exam flow: current question, revealing the answer, grading, flagging
and abandoning the session in progress.

A request never keeps a ``SessionRunner`` across requests - a web request is
stateless, and a phone browser can drop its tab at any moment - so every
handler rebuilds one fresh from the database via ``examsession.resume_session``.
See ``docs/adr/0001-persist-session-plan.md`` for why that is safe and cheap.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from api.modules.examsession import api as examsession
from web.deps import get_db, require_login
from web.templating import templates
from web.urls import image_url

router = APIRouter()


def _active_runner(conn: sqlite3.Connection) -> examsession.SessionRunner | None:
    """The runner for the session in progress, or ``None`` if there is none."""
    active = examsession.get_active_session(conn)
    if active is None:
        return None
    return examsession.resume_session(conn, active.id)


def _finish_and_redirect(runner: examsession.SessionRunner) -> RedirectResponse:
    runner.finish()
    return RedirectResponse(url=f"/results/{runner.session.id}", status_code=303)


def _abandon_and_redirect(runner: examsession.SessionRunner) -> RedirectResponse:
    runner.abandon()
    return RedirectResponse(url=f"/results/{runner.session.id}", status_code=303)


def _render_question(request: Request, conn: sqlite3.Connection, runner, revealed: bool):
    question = runner.current()
    preload = [image_url(question.answer_image if not revealed else question.question_image)]
    upcoming = runner.upcoming()
    if upcoming is not None:
        preload.append(image_url(upcoming.question_image))

    return templates.TemplateResponse(
        request,
        "exam_question.html",
        {
            "runner": runner,
            "question": question,
            "revealed": revealed,
            "question_image_url": image_url(question.question_image),
            "answer_image_url": image_url(question.answer_image),
            "preload_urls": preload,
        },
    )


@router.get("/exam")
def show_question(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
):
    runner = _active_runner(conn)
    if runner is None:
        return RedirectResponse(url="/", status_code=303)
    if runner.finished:
        return _finish_and_redirect(runner)
    if examsession.get_auto_reveal(conn):
        return RedirectResponse(url="/exam/answer", status_code=303)
    return _render_question(request, conn, runner, revealed=False)


@router.get("/exam/answer")
def show_answer(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
):
    runner = _active_runner(conn)
    if runner is None:
        return RedirectResponse(url="/", status_code=303)
    if runner.finished:
        return _finish_and_redirect(runner)
    return _render_question(request, conn, runner, revealed=True)


@router.post("/exam/grade")
def grade_question(
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
    grade: str = Form(...),
):
    runner = _active_runner(conn)
    if runner is None:
        return RedirectResponse(url="/", status_code=303)
    if grade in examsession.GRADES and not runner.finished:
        runner.grade(grade)
    if runner.finished:
        return _finish_and_redirect(runner)
    return RedirectResponse(url="/exam", status_code=303)


@router.post("/exam/flag")
def flag(
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
):
    runner = _active_runner(conn)
    if runner is None:
        return RedirectResponse(url="/", status_code=303)
    if not runner.finished:
        # Always followed by the fresh, unrevealed replacement - matching the
        # desktop screen, which resets to "answer not shown" after a flag
        # even when the flag was raised while the answer was on screen.
        runner.flag_broken()
    if runner.finished:
        return _finish_and_redirect(runner)
    return RedirectResponse(url="/exam", status_code=303)


@router.post("/exam/abandon")
def abandon(
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
):
    runner = _active_runner(conn)
    if runner is None:
        return RedirectResponse(url="/", status_code=303)
    if runner.finished:
        return _finish_and_redirect(runner)
    return _abandon_and_redirect(runner)
