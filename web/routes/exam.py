"""
The core exam flow: the current question, revealing the answer, grading,
postponing a question to come back to it, the review screen, flagging a badly
cropped question, and finishing or abandoning the session in progress.

A request never keeps a ``SessionRunner`` across requests - a web request is
stateless, and a phone browser can drop its tab at any moment - so every
handler rebuilds one fresh from the database via ``examsession.resume_session``.
See ``docs/adr/0001-persist-session-plan.md`` for why that is safe and cheap.

Two ways to reach a question: ``/exam`` shows the one the normal pass is on (the
first that is neither graded nor postponed), and ``/exam/q/{position}`` opens
any question of the session by its place in the plan, which is how the review
screen sends the user back to a postponed or already graded one.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response

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


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=303)


def _finish_and_redirect(runner: examsession.SessionRunner) -> RedirectResponse:
    runner.finish()
    return _redirect(f"/results/{runner.session.id}")


def _abandon_and_redirect(runner: examsession.SessionRunner) -> RedirectResponse:
    runner.abandon()
    return _redirect(f"/results/{runner.session.id}")


def _render_question(
    request: Request,
    runner: examsession.SessionRunner,
    position: int,
    revealed: bool,
    question_url: str,
    answer_url: str,
) -> Response:
    question = runner.question_at(position)
    entry = runner.entry_at(position)
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
            "position": position,
            "entry": entry,
            "revealed": revealed,
            "question_url": question_url,
            "answer_url": answer_url,
            "question_image_url": image_url(question.question_image),
            "answer_image_url": image_url(question.answer_image),
            "preload_urls": preload,
        },
    )


def _where_the_pass_stands(runner: examsession.SessionRunner) -> RedirectResponse | None:
    """
    Where to send the user when the normal pass has no question left to show.

    Returns ``None`` while a question is waiting. Once nothing is left: with
    postponed questions the review screen opens, otherwise the test is over.
    """
    if runner.complete:
        return _finish_and_redirect(runner)
    if runner.finished:
        return _redirect("/exam/review")
    return None


@router.get("/exam")
def show_question(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
):
    runner = _active_runner(conn)
    if runner is None:
        return _redirect("/")
    if (done := _where_the_pass_stands(runner)) is not None:
        return done
    if examsession.get_auto_reveal(conn):
        return _redirect("/exam/answer")
    return _render_question(request, runner, runner.position, False, "/exam", "/exam/answer")


@router.get("/exam/answer")
def show_answer(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
):
    runner = _active_runner(conn)
    if runner is None:
        return _redirect("/")
    if (done := _where_the_pass_stands(runner)) is not None:
        return done
    return _render_question(request, runner, runner.position, True, "/exam", "/exam/answer")


@router.get("/exam/q/{position}")
def show_question_at(
    position: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
):
    runner = _active_runner(conn)
    if runner is None:
        return _redirect("/")
    if runner.question_at(position) is None:
        return _redirect("/exam/review")
    base = f"/exam/q/{position}"
    if examsession.get_auto_reveal(conn):
        return _redirect(f"{base}/answer")
    return _render_question(request, runner, position, False, base, f"{base}/answer")


@router.get("/exam/q/{position}/answer")
def show_answer_at(
    position: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
):
    runner = _active_runner(conn)
    if runner is None:
        return _redirect("/")
    if runner.question_at(position) is None:
        return _redirect("/exam/review")
    base = f"/exam/q/{position}"
    return _render_question(request, runner, position, True, base, f"{base}/answer")


@router.get("/exam/review")
def review(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
):
    runner = _active_runner(conn)
    if runner is None:
        return _redirect("/")

    entries = runner.entries()
    marked = runner.marked_positions
    return templates.TemplateResponse(
        request,
        "exam_review.html",
        {
            "runner": runner,
            "entries": entries,
            "graded": runner.graded,
            "marked_count": len(marked),
            "first_marked": marked[0] if marked else None,
            "pending_count": sum(1 for e in entries if e.state == examsession.ENTRY_PENDING),
            "unresolved": runner.unresolved,
        },
    )


@router.post("/exam/grade")
def grade_question(
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
    grade: str = Form(...),
    position: int | None = Form(None),
):
    runner = _active_runner(conn)
    if runner is None:
        return _redirect("/")
    if grade in examsession.GRADES:
        try:
            runner.grade(grade, position)
        except ValueError:
            return _redirect("/exam/review")
    return _redirect("/exam")


@router.post("/exam/mark")
def mark_question(
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
    position: int | None = Form(None),
):
    """Postpone a question: no grade, not counted for now, to come back to later."""
    runner = _active_runner(conn)
    if runner is None:
        return _redirect("/")
    try:
        runner.mark(position)
    except ValueError:
        return _redirect("/exam/review")
    return _redirect("/exam")


@router.post("/exam/unmark")
def unmark_question(
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
    position: int = Form(...),
):
    runner = _active_runner(conn)
    if runner is None:
        return _redirect("/")
    try:
        runner.unmark(position)
    except ValueError:
        return _redirect("/exam/review")
    return _redirect(f"/exam/q/{position}")


@router.post("/exam/flag")
def flag(
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
    position: int | None = Form(None),
):
    runner = _active_runner(conn)
    if runner is None:
        return _redirect("/")
    try:
        # Always followed by the fresh, unrevealed replacement - matching the
        # desktop screen, which resets to "answer not shown" after a flag
        # even when the flag was raised while the answer was on screen.
        runner.flag_broken(position)
    except ValueError:
        return _redirect("/exam/review")
    return _redirect("/exam")


@router.post("/exam/finish")
def finish(
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
):
    """End the test from the review screen. Questions without a grade stay uncounted."""
    runner = _active_runner(conn)
    if runner is None:
        return _redirect("/")
    return _finish_and_redirect(runner)


@router.post("/exam/abandon")
def abandon(
    conn: sqlite3.Connection = Depends(get_db),
    _: str = Depends(require_login),
):
    runner = _active_runner(conn)
    if runner is None:
        return _redirect("/")
    if runner.complete:
        return _finish_and_redirect(runner)
    return _abandon_and_redirect(runner)
