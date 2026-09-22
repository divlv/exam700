"""
Shared FastAPI dependencies: a database connection per request and the login gate.

Kept deliberately small - every route in ``web/routes`` depends on one or both of
these, so behaviour changes (connection settings, what "logged in" means) only
need to happen here.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from fastapi import Request

from api.modules.shared import api as shared

#: The one login this application accepts. There is no password: the point is
#: to keep the exam content off the open internet, not to protect a secret.
AUTHORIZED_LOGIN = "dima"

#: Session key holding the logged-in user's name, once authenticated.
SESSION_USER_KEY = "user"


class NotAuthenticated(Exception):
    """Raised by :func:`require_login` and turned into a redirect to /login."""


def get_db() -> Iterator[sqlite3.Connection]:
    """
    Open one SQLite connection for the duration of a request.

    The desktop GUI keeps a single connection open for the whole process,
    which works for a one-threaded Tk app but not for a web server handling
    independent requests. A per-request connection is simple and correct for
    a single-user deployment with no connection pool.

    Mutating calls in ``questionbank.repo`` do not commit themselves (the
    caller owns the transaction, by design of that module); committing once
    here after the route handler runs is this application's equivalent of the
    desktop GUI screens calling ``conn.commit()`` after each action.
    """
    conn = shared.get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def require_login(request: Request) -> str:
    """
    FastAPI dependency that guards every non-public route.

    Returns:
        The logged-in user's name.

    Raises:
        NotAuthenticated: If the session cookie does not carry the expected
            user. Handled by a top-level exception handler that redirects to
            ``/login`` instead of returning a bare 401/403.
    """
    user = request.session.get(SESSION_USER_KEY)
    if user != AUTHORIZED_LOGIN:
        raise NotAuthenticated()
    return user
