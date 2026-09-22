"""
Shared fixtures for the web layer's HTTP-level tests.

These exercise real routes through Starlette's TestClient rather than calling
``api.modules.*`` directly, so they catch what the module tests cannot:
template errors, wrong context keys, redirect targets, and the login gate.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from api.modules.examsession import api as examsession
from api.modules.questionbank import api as questionbank
from api.modules.shared import api as shared
from api.modules.shared.internal import _db as db_internal

#: A minimal valid 1x1 PNG, so /img/ requests have a real file to serve
#: without pulling in Pillow just for test fixtures.
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _make_question(number: int) -> questionbank.Question:
    return questionbank.Question(
        id=number,
        source_file="az-700_exam_questions_with_answers_01.pdf",
        start_page=0,
        end_page=1,
        question_image=f"images/q{number:04d}_question.png",
        answer_image=f"images/q{number:04d}_answer.png",
        geometry={"dpi": 200},
    )


@pytest.fixture()
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    """
    A TestClient wired to a throw-away database and image directory.

    Patches ``shared.api``'s path constants directly (rather than the
    ``AZ700_DATA_DIR`` environment variable) because those constants are
    computed once at import time, and some other test module may already have
    imported ``api.modules.shared.api`` earlier in the same pytest session.
    """
    data_dir = tmp_path / "data"
    images_dir = data_dir / "images"
    images_dir.mkdir(parents=True)
    db_path = data_dir / "az700.sqlite"

    monkeypatch.setattr(shared, "DATA_DIR", data_dir)
    monkeypatch.setattr(shared, "IMAGES_DIR", images_dir)
    monkeypatch.setattr(shared, "REPORT_DIR", data_dir / "report")
    monkeypatch.setattr(shared, "DB_PATH", db_path)
    monkeypatch.setenv("AZ700_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("AZ700_SESSION_SECURE", "0")

    conn = db_internal.connect(db_path)
    questionbank.ensure_schema(conn)
    examsession.ensure_schema(conn)
    for number in (1, 2, 3):
        questionbank.upsert_question(conn, _make_question(number))
        (images_dir / f"q{number:04d}_question.png").write_bytes(_TINY_PNG)
        (images_dir / f"q{number:04d}_answer.png").write_bytes(_TINY_PNG)
    conn.commit()
    conn.close()

    from web.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture()
def logged_in_client(client: TestClient) -> TestClient:
    """The same client, already authenticated as the one recognised login."""
    response = client.post("/login", data={"login": "dima", "next": "/"})
    assert response.status_code == 200  # redirects were followed to "/"
    return client
