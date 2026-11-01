"""
Login and logout.

The application has exactly one accepted login and no password. The login is not
in the source (the repository is public): it comes from the environment, see
``web.credentials``, and works like a password. This is a personal exam trainer,
not a multi-user system.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from api.modules.shared import api as shared
from web import credentials
from web.deps import SESSION_USER_KEY
from web.templating import templates

logger = shared.get_logger(__name__)

router = APIRouter()

#: Shown when no login is configured. It says nothing about how to configure
#: one: the page is public, the details are in the log and the deployment guide.
_NOT_CONFIGURED_MESSAGE = "Вход не настроен на сервере."


def _safe_next(target: str) -> str:
    """
    Keep only a path on this site to redirect to after login.

    ``//host`` and ``/\\host`` are what browsers read as another site, so they are
    refused along with anything that is not an absolute path.
    """
    if target.startswith("/") and not target.startswith(("//", "/\\")):
        return target
    return "/"


def _client(request: Request) -> str:
    return request.client.host if request.client else "-"


def _login_page(request: Request, next_path: str, error: str | None, status_code: int) -> object:
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": error, "next": next_path, "logged_in": False},
        status_code=status_code,
    )


@router.get("/login")
def login_form(request: Request, next: str = "/") -> object:
    """Show the login form, or skip straight past it if already logged in."""
    if credentials.session_is_valid(request.session.get(SESSION_USER_KEY)):
        return RedirectResponse(url=_safe_next(next), status_code=303)
    if not credentials.is_configured():
        return _login_page(request, next, _NOT_CONFIGURED_MESSAGE, 503)
    return _login_page(request, next, None, 200)


@router.post("/login")
def login_submit(
    request: Request, login: str = Form(...), next: str = Form("/")
) -> object:
    """Accept the one recognised login, otherwise redisplay the form with an error."""
    if not credentials.is_configured():
        logger.error("login refused: %s is not set, nobody can log in", credentials.ENV_LOGIN)
        return _login_page(request, next, _NOT_CONFIGURED_MESSAGE, 503)

    if credentials.login_matches(login):
        request.session[SESSION_USER_KEY] = credentials.session_token()
        logger.info("login ok: client=%s", _client(request))
        return RedirectResponse(url=_safe_next(next), status_code=303)

    # What was typed is not logged: a near miss of a secret is still half a secret.
    logger.warning("login failed: client=%s", _client(request))
    return _login_page(request, next, "Неизвестный логин.", 401)


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    """Clear the session and return to the login screen."""
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
