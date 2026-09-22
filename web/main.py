"""
FastAPI application factory for the AZ-700 exam trainer.

Wires together the reused ``api.modules.*`` backend, session-cookie auth, the
Jinja2 screens and the static assets. Run with:

    uvicorn web.main:app --host 0.0.0.0 --port 8000

Configuration is entirely through environment variables, read here and in
``api.modules.shared.api`` - there is no config file, matching the desktop
build's "no configuration system" baseline plus the minimum the web deployment
needs on top of it.
"""

from __future__ import annotations

import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from api.modules.examsession import api as examsession
from api.modules.questionbank import api as questionbank
from api.modules.shared import api as shared
from web import auth
from web.deps import NotAuthenticated
from web.routes import admin, exam, health, images, menu, results, session, settings

logger = shared.get_logger(__name__)

#: Resolved relative to this file, not the process's working directory - see
#: web/templating.py for why the same applies to the Jinja2 templates.
_WEB_DIR = Path(__file__).resolve().parent

#: Cookie lifetime: long enough that a phone is not logged out between study
#: sessions days apart. There is no password to make re-entering it painful,
#: but there is no reason to ask for it more than necessary either.
_SESSION_MAX_AGE_SECONDS = 90 * 24 * 60 * 60


def _session_secret() -> str:
    """
    Read the cookie-signing key, or fall back to an ephemeral one for local use.

    In the container this must come from ``AZ700_SESSION_SECRET`` (a k8s
    Secret) so sessions survive a pod restart. Falling back instead of failing
    startup keeps ``pytest`` and a quick local ``uvicorn`` run working without
    extra setup; the warning makes the trade-off visible rather than silent.
    """
    secret = os.environ.get("AZ700_SESSION_SECRET")
    if secret:
        return secret
    logger.warning(
        "AZ700_SESSION_SECRET is not set; using a random secret for this process. "
        "Every login will be forgotten on restart. Set it via a k8s Secret in production."
    )
    return secrets.token_hex(32)


def _cookies_require_https() -> bool:
    """
    Whether the session cookie is marked Secure.

    Defaults to true (the deployed app is only ever reached over HTTPS, behind
    Traefik). Set ``AZ700_SESSION_SECURE=0`` for local ``http://`` testing.
    """
    return os.environ.get("AZ700_SESSION_SECURE", "1") != "0"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Run schema migrations once at startup, before any request is served."""
    shared.ensure_directories()
    conn = shared.get_connection()
    try:
        questionbank_version = questionbank.ensure_schema(conn)
        examsession_version = examsession.ensure_schema(conn)
    finally:
        conn.close()

    logger.info(
        "startup: run_id=%s data_dir=%s db_path=%s schema questionbank=v%d examsession=v%d",
        shared.get_run_id(),
        shared.DATA_DIR,
        shared.DB_PATH,
        questionbank_version,
        examsession_version,
    )
    yield
    logger.info("shutdown: run_id=%s", shared.get_run_id())


def create_app() -> FastAPI:
    """Build and fully wire the FastAPI application."""
    app = FastAPI(title="AZ-700 Exam Trainer", lifespan=_lifespan)

    app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")

    app.add_middleware(
        SessionMiddleware,
        secret_key=_session_secret(),
        max_age=_SESSION_MAX_AGE_SECONDS,
        same_site="lax",
        https_only=_cookies_require_https(),
    )

    @app.exception_handler(NotAuthenticated)
    async def _redirect_to_login(request: Request, exc: NotAuthenticated) -> RedirectResponse:
        """Send an unauthenticated visitor to the login form, remembering where they were."""
        next_path = request.url.path
        if request.url.query:
            next_path = f"{next_path}?{request.url.query}"
        return RedirectResponse(url=f"/login?next={quote(next_path)}", status_code=303)

    @app.exception_handler(Exception)
    async def _log_unhandled_exception(request: Request, exc: Exception) -> PlainTextResponse:
        """
        Log every unhandled exception with the run id before showing a generic error.

        Mirrors the desktop GUI's ``report_callback_exception``: the user never
        sees a traceback, but every one is on record with the run id to find it
        by.
        """
        logger.exception("unhandled exception on %s %s", request.method, request.url.path)
        return PlainTextResponse(
            "Произошла внутренняя ошибка. Подробности записаны в журнал сервера "
            f"(RunId: {shared.get_run_id()}).",
            status_code=500,
        )

    app.include_router(auth.router)
    app.include_router(menu.router)
    app.include_router(session.router)
    app.include_router(exam.router)
    app.include_router(results.router)
    app.include_router(admin.router)
    app.include_router(settings.router)
    app.include_router(images.router)
    app.include_router(health.router)

    @app.get("/manifest.json", include_in_schema=False)
    def manifest() -> FileResponse:
        return FileResponse(
            str(_WEB_DIR / "static" / "manifest.json"),
            media_type="application/manifest+json",
        )

    return app


app = create_app()
