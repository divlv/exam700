"""Login gate: redirect when unauthenticated, accept only the one known login."""

from __future__ import annotations


def test_root_redirects_to_login_when_not_authenticated(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_wrong_login_is_rejected(client):
    response = client.post("/login", data={"login": "someone-else", "next": "/"})
    assert response.status_code == 401
    assert "Неизвестный логин" in response.text


def test_correct_login_grants_access(client):
    response = client.post("/login", data={"login": "dima", "next": "/"})
    assert response.status_code == 200
    assert "AZ-700" in response.text


def test_login_is_case_and_whitespace_insensitive(client):
    """The login is not a secret - it only needs to be recognisable, not exact."""
    response = client.post("/login", data={"login": "  DIMA  ", "next": "/"})
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
