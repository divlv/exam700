"""
Login and logout.

The application has exactly one accepted login and no password (see
``web.deps.AUTHORIZED_LOGIN``): this is a personal exam trainer, not a
multi-user system, and the login exists only to keep it off the open internet.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from web.deps import AUTHORIZED_LOGIN, SESSION_USER_KEY
from web.templating import templates

router = APIRouter()


@router.get("/login")
def login_form(request: Request, next: str = "/") -> object:
    """Show the login form, or skip straight past it if already logged in."""
    if request.session.get(SESSION_USER_KEY) == AUTHORIZED_LOGIN:
        return RedirectResponse(url=next, status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"error": None, "next": next, "logged_in": False}
    )


@router.post("/login")
def login_submit(
    request: Request, login: str = Form(...), next: str = Form("/")
) -> object:
    """Accept the one recognised login, otherwise redisplay the form with an error."""
    if login.strip().lower() == AUTHORIZED_LOGIN:
        request.session[SESSION_USER_KEY] = AUTHORIZED_LOGIN
        # A relative path only: never redirect off-site based on user input.
        target = next if next.startswith("/") else "/"
        return RedirectResponse(url=target, status_code=303)

    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": "Неизвестный логин.", "next": next, "logged_in": False},
        status_code=401,
    )


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    """Clear the session and return to the login screen."""
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
