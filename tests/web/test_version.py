"""The short git commit shown in every page footer, which tells versions apart."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from web import version
from web.templating import templates


def test_the_build_time_commit_is_shortened_to_seven_characters(monkeypatch):
    monkeypatch.setenv(version.ENV_VAR, "2aa4b37c1d9e0f5a6b7c8d9e0f1a2b3c4d5e6f70")

    assert version.get_git_commit() == "2aa4b37"


def test_a_short_commit_is_kept_as_it_is(monkeypatch):
    monkeypatch.setenv(version.ENV_VAR, "2aa4b37")

    assert version.get_git_commit() == "2aa4b37"


def test_without_a_commit_or_a_checkout_it_says_unknown(monkeypatch, tmp_path):
    monkeypatch.delenv(version.ENV_VAR, raising=False)
    monkeypatch.setattr(version, "_REPO_ROOT", tmp_path)  # not a git checkout

    assert version.get_git_commit() == "unknown"


def test_the_image_default_of_unknown_falls_back_to_git(monkeypatch, tmp_path):
    """A Docker build without the build arg leaves ``unknown``, which must not be shown as a hash."""
    monkeypatch.setenv(version.ENV_VAR, "unknown")
    monkeypatch.setattr(version, "_REPO_ROOT", tmp_path)

    assert version.get_git_commit() == "unknown"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_a_local_run_asks_git(monkeypatch):
    monkeypatch.delenv(version.ENV_VAR, raising=False)
    expected = subprocess.run(
        ["git", "rev-parse", "--short=7", "HEAD"],
        cwd=version._REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if expected.returncode != 0:
        pytest.skip("not running inside a git checkout")

    assert version.get_git_commit() == expected.stdout.strip()[:7]


def test_every_page_shows_the_commit_in_its_footer(monkeypatch, logged_in_client):
    monkeypatch.setitem(templates.env.globals, "GIT_COMMIT", "2aa4b37")
    footer = '<footer class="page-footer">2aa4b37</footer>'

    for path in ("/", "/session/new", "/results", "/admin", "/settings"):
        assert footer in logged_in_client.get(path).text, path

    logged_in_client.post("/session/new", data={"count": "3"})
    assert footer in logged_in_client.get("/exam").text
    assert footer in logged_in_client.get("/exam/answer").text


def test_the_login_page_shows_the_commit_too(monkeypatch, client):
    monkeypatch.setitem(templates.env.globals, "GIT_COMMIT", "2aa4b37")

    assert '<footer class="page-footer">2aa4b37</footer>' in client.get("/login").text
