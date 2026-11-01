"""
Login gate: redirect when unauthenticated, accept only the one configured login.

The login is a secret taken from ``AZ_700_LOGIN`` (not from the source, which is
public), so these tests also cover what a secret needs: it is case-sensitive, it
is not copied into the cookie or the log, there is no fallback when it is unset,
and changing it ends the sessions started with the old one.
"""

from __future__ import annotations

import base64
import json
import logging

import pytest

from web import auth, credentials


def _session_cookie_payload(client) -> dict:
    """Decode the readable (signed, not encrypted) part of the session cookie."""
    cookie = client.cookies.get("session")
    assert cookie, "no session cookie was set"
    segment = cookie.split(".")[0]
    return json.loads(base64.b64decode(segment + "=" * (-len(segment) % 4)))


# --- the gate -------------------------------------------------------------


def test_root_redirects_to_login_when_not_authenticated(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_wrong_login_is_rejected(client):
    response = client.post("/login", data={"login": "someone-else", "next": "/"})
    assert response.status_code == 401
    assert "Неизвестный логин" in response.text


def test_correct_login_grants_access(client, test_login):
    response = client.post("/login", data={"login": test_login, "next": "/"})
    assert response.status_code == 200
    assert "AZ-700" in response.text


def test_the_old_hardcoded_login_no_longer_works(client):
    """It is in the public repository, so it must not open anything."""
    response = client.post("/login", data={"login": "dima", "next": "/"})
    assert response.status_code == 401


def test_login_is_case_sensitive(client, test_login):
    """It is a secret, not a name: a different case is a different value."""
    assert test_login != test_login.lower()
    response = client.post("/login", data={"login": test_login.lower(), "next": "/"})
    assert response.status_code == 401


def test_surrounding_whitespace_is_ignored(client, test_login):
    """A phone keyboard may append a space."""
    response = client.post("/login", data={"login": f"  {test_login}  ", "next": "/"})
    assert response.status_code == 200


def test_logout_returns_to_login(logged_in_client):
    response = logged_in_client.post("/logout")
    assert response.status_code == 200
    assert response.url.path == "/login"

    protected = logged_in_client.get("/", follow_redirects=False)
    assert protected.status_code == 303


def test_unauthenticated_redirect_preserves_the_original_path(client):
    """A phone that reopens a bookmarked deep link lands back on it after login."""
    response = client.get("/settings", follow_redirects=False)
    assert response.headers["location"] == "/login?next=/settings"


# --- the login is a secret --------------------------------------------------


def test_the_cookie_holds_a_fingerprint_not_the_login(logged_in_client, test_login):
    payload = _session_cookie_payload(logged_in_client)
    raw = json.dumps(payload)

    assert test_login not in raw
    assert payload["user"] == credentials.session_token()


def test_changing_the_login_ends_the_sessions_started_with_the_old_one(logged_in_client, monkeypatch):
    assert logged_in_client.get("/", follow_redirects=False).status_code == 200

    monkeypatch.setenv("AZ_700_LOGIN", "a-different-login-value")

    assert logged_in_client.get("/", follow_redirects=False).status_code == 303


@pytest.mark.parametrize("value", [None, "", "   "])
def test_without_a_configured_login_nobody_can_get_in(logged_in_client, monkeypatch, value):
    """There is no fallback value: a built-in login would be public again."""
    if value is None:
        monkeypatch.delenv("AZ_700_LOGIN")
    else:
        monkeypatch.setenv("AZ_700_LOGIN", value)

    # Not even a session that was valid a moment ago.
    assert logged_in_client.get("/", follow_redirects=False).status_code == 303
    page = logged_in_client.get("/login")
    assert page.status_code == 503
    assert "Вход не настроен" in page.text
    for typed in ("anything", "dima", "None", "   x   "):
        attempt = logged_in_client.post("/login", data={"login": typed, "next": "/"})
        assert attempt.status_code == 503, typed


def test_the_not_configured_page_does_not_name_the_variable(client, monkeypatch):
    monkeypatch.delenv("AZ_700_LOGIN")

    assert "AZ_700_LOGIN" not in client.get("/login").text


def test_the_form_masks_the_login_and_stops_the_phone_keyboard_changing_it(client):
    page = client.get("/login").text

    assert 'type="password"' in page
    assert 'autocapitalize="off"' in page
    assert 'autocorrect="off"' in page
    assert 'spellcheck="false"' in page


def test_the_log_records_a_failed_login_but_not_what_was_typed(client, test_login):
    lines: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(record.getMessage())

    handler = Capture(level=logging.DEBUG)
    auth.logger.addHandler(handler)
    previous = auth.logger.level
    auth.logger.setLevel(logging.DEBUG)
    try:
        client.post("/login", data={"login": "almost-the-login-xyz", "next": "/"})
        client.post("/login", data={"login": test_login, "next": "/"})
    finally:
        auth.logger.removeHandler(handler)
        auth.logger.setLevel(previous)

    joined = "\n".join(lines)
    assert "login failed" in joined and "login ok" in joined
    assert "almost-the-login-xyz" not in joined
    assert test_login not in joined


# --- where login sends you afterwards ---------------------------------------


@pytest.mark.parametrize(
    "target",
    ["//evil.example", "///evil.example", "https://evil.example", "/\\evil.example", "evil.example", ""],
)
def test_login_never_redirects_off_the_site(client, test_login, target):
    response = client.post(
        "/login", data={"login": test_login, "next": target}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_login_redirects_to_a_path_on_the_site(client, test_login):
    response = client.post(
        "/login", data={"login": test_login, "next": "/settings"}, follow_redirects=False
    )

    assert response.headers["location"] == "/settings"


def test_an_already_logged_in_visitor_is_not_sent_off_the_site_either(logged_in_client):
    response = logged_in_client.get("/login?next=//evil.example", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


# --- the credentials helpers ------------------------------------------------


def test_the_session_token_depends_on_the_login(monkeypatch):
    monkeypatch.setenv("AZ_700_LOGIN", "first")
    first = credentials.session_token()
    monkeypatch.setenv("AZ_700_LOGIN", "second")

    assert first != credentials.session_token()
    assert credentials.session_is_valid(first) is False


@pytest.mark.parametrize("stored", [None, 123, ["x"], "", "not-the-token"])
def test_a_session_value_that_is_not_the_token_is_not_valid(monkeypatch, stored):
    monkeypatch.setenv("AZ_700_LOGIN", "some-login")

    assert credentials.session_is_valid(stored) is False


def test_unicode_login_can_be_compared(monkeypatch):
    """compare_digest on text would reject non-ASCII; the comparison is on bytes."""
    monkeypatch.setenv("AZ_700_LOGIN", "логин-ключ")

    assert credentials.login_matches("логин-ключ") is True
    assert credentials.login_matches("логин-ключ2") is False
