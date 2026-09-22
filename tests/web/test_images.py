"""Serving the prepared PNGs: behind login, cached forever, name-validated."""

from __future__ import annotations


def test_image_requires_login(client):
    response = client.get("/img/q0001_question.png", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_image_is_served_with_an_immutable_cache_header(logged_in_client):
    response = logged_in_client.get("/img/q0001_question.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert "immutable" in response.headers["cache-control"]


def test_unknown_image_name_is_rejected(logged_in_client):
    response = logged_in_client.get("/img/q0001_question.png.bak")
    assert response.status_code == 404


def test_a_wellformed_but_missing_image_is_404(logged_in_client):
    response = logged_in_client.get("/img/q9999_question.png")
    assert response.status_code == 404


def test_healthz_needs_no_login(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.text == "ok"
