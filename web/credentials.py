"""
The one accepted login, taken from the environment instead of the source code.

The code is public, so a login written in it protects nothing. It is read from
``AZ_700_LOGIN`` (a Kubernetes Secret in the cluster, a plain variable on a
developer machine) and behaves like a password: it is compared in constant time,
it is never stored in the session cookie, and it is never logged.

If the variable is not set nobody can log in. There is deliberately no built-in
fallback value: a fallback would be a login that is public again.
"""

from __future__ import annotations

import hashlib
import hmac
import os

#: Environment variable holding the login. Read on every use, so a change (and a
#: test's ``monkeypatch``) takes effect without rebuilding anything.
ENV_LOGIN = "AZ_700_LOGIN"


def configured_login() -> str | None:
    """The login, or ``None`` if the variable is missing or blank."""
    value = os.environ.get(ENV_LOGIN, "").strip()
    return value or None


def is_configured() -> bool:
    """Whether anyone can log in at all."""
    return configured_login() is not None


def login_matches(candidate: str) -> bool:
    """
    Check what was typed against the login, in constant time.

    Surrounding whitespace is ignored (a phone keyboard may add a space); case
    matters, because the value is a secret and not a name.
    """
    login = configured_login()
    if login is None:
        return False
    return hmac.compare_digest(candidate.strip().encode("utf-8"), login.encode("utf-8"))


def session_token() -> str | None:
    """
    What the session cookie stores to say "logged in", or ``None`` if not configured.

    A fingerprint of the login, not the login: the cookie is signed but its
    content can be read. As a side effect, changing the login invalidates every
    session that was started with the old one.
    """
    login = configured_login()
    if login is None:
        return None
    return hashlib.sha256(login.encode("utf-8")).hexdigest()


def session_is_valid(stored: object) -> bool:
    """Whether a value read from the session cookie belongs to the current login."""
    token = session_token()
    if token is None or not isinstance(stored, str):
        return False
    return hmac.compare_digest(stored.encode("utf-8"), token.encode("utf-8"))
