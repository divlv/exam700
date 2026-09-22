"""Liveness/readiness probe. Deliberately not behind login: k8s calls it directly."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from api.modules.shared import api as shared

router = APIRouter()


@router.get("/healthz")
def healthz() -> PlainTextResponse:
    """
    Confirm the process can reach its database.

    Kept cheap on purpose - one ``SELECT 1``, no schema checks. Startup
    migrations already ran in the application's lifespan handler before the
    process started accepting any connections.
    """
    conn = shared.get_connection()
    try:
        conn.execute("SELECT 1").fetchone()
    finally:
        conn.close()
    return PlainTextResponse("ok")
