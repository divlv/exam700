"""
The AI check through HTTP: the popup's endpoints, the answer page and the list.

The model is a fake installed into the running application, so nothing here
reaches the network and the number of "paid" requests can be counted.
"""

from __future__ import annotations

import threading
import time

import pytest

from api.modules.aiassist import api as aiassist
from api.modules.aiassist.internal._client import AiRequestError, AzureConfig, ModelReply

CONFIG = AzureConfig(
    base_url="https://example.openai.azure.com/openai/v1/",
    api_key="web-test-key-not-a-secret",
    deployment="web-test-deployment",
)
HTML = '<div class="verdict">✅ Ответ: C</div><p>Потому что так.</p>'


class FakeModel:
    def __init__(self) -> None:
        self.calls: list[int] = []
        self.release = threading.Event()
        self.release.set()
        self.error: Exception | None = None
        self.summary = "Выбор SKU шлюза"
        self.answer = "C"

    def __call__(self, config: AzureConfig, question_id: int, png: bytes) -> ModelReply:
        self.calls.append(question_id)
        assert self.release.wait(timeout=10)
        if self.error:
            raise self.error
        return ModelReply(
            summary=self.summary,
            answer=self.answer,
            html=HTML,
            model="web-test-model",
            response_id="resp_web",
            input_tokens=1000,
            output_tokens=500,
        )


@pytest.fixture()
def model(logged_in_client) -> FakeModel:
    """A configured service with a fake model, put in place of the started one."""
    fake = FakeModel()
    aiassist.install_service(aiassist.AiService(load_config=lambda: CONFIG, ask=fake))
    yield fake
    aiassist.install_service(None)


def _unconfigure() -> None:
    def missing() -> AzureConfig:
        raise aiassist.AiNotConfigured("Not set: AZURE_OPENAI_API_KEY")

    aiassist.install_service(aiassist.AiService(load_config=missing))


def _settle(client, question_id: int) -> dict:
    """Poll the status endpoint like the popup does."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        data = client.get(f"/ai/{question_id}/status").json()
        if data["state"] != "running":
            return data
        time.sleep(0.01)
    raise AssertionError("the check never finished")


# --- the gate -----------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [("post", "/ai/1"), ("get", "/ai/1/status"), ("get", "/ai/1/view"), ("get", "/ai")],
)
def test_every_ai_route_requires_login(client, method, path):
    response = getattr(client, method)(path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


# --- asking -------------------------------------------------------------


def test_asking_starts_the_check_and_the_answer_is_polled_until_done(logged_in_client, model):
    started = logged_in_client.post("/ai/1")
    assert started.status_code in (200, 202)

    done = _settle(logged_in_client, 1)

    assert done["state"] == "done"
    assert done["meta"]["answer"] == "C"
    assert done["meta"]["summary"] == "Выбор SKU шлюза"
    assert done["meta"]["model"] == "web-test-model"
    assert done["meta"]["input_tokens"] == 1000
    assert model.calls == [1]


def test_asking_again_returns_the_stored_answer_without_another_request(logged_in_client, model):
    logged_in_client.post("/ai/1")
    _settle(logged_in_client, 1)

    again = logged_in_client.post("/ai/1")

    assert again.status_code == 200
    assert again.json()["state"] == "done"
    assert model.calls == [1]


def test_a_check_in_progress_is_reported_and_joined_not_repeated(logged_in_client, model):
    model.release.clear()

    first = logged_in_client.post("/ai/1")
    second = logged_in_client.post("/ai/1")
    status = logged_in_client.get("/ai/1/status").json()
    model.release.set()
    _settle(logged_in_client, 1)

    assert first.status_code == second.status_code == 202
    assert status["state"] == "running" and status["elapsed"] >= 0
    assert model.calls == [1]


def test_a_failed_check_reports_the_error_and_can_be_retried(logged_in_client, model):
    model.error = AiRequestError("Модель не ответила вовремя.")
    logged_in_client.post("/ai/1")

    failed = _settle(logged_in_client, 1)
    assert failed == {"state": "error", "message": "Модель не ответила вовремя."}

    model.error = None
    logged_in_client.post("/ai/1")
    assert _settle(logged_in_client, 1)["state"] == "done"
    assert model.calls == [1, 1]


def test_a_question_that_does_not_exist_is_404(logged_in_client, model):
    assert logged_in_client.post("/ai/999").status_code == 404
    assert logged_in_client.get("/ai/999/status").status_code == 404
    assert model.calls == []


def test_status_of_a_question_never_asked_is_idle(logged_in_client, model):
    assert logged_in_client.get("/ai/2/status").json() == {"state": "idle"}


def test_without_settings_asking_is_refused_with_503(logged_in_client):
    _unconfigure()
    try:
        response = logged_in_client.post("/ai/1")
    finally:
        aiassist.install_service(None)

    assert response.status_code == 503
    body = response.json()
    assert body["state"] == "unconfigured"
    assert "AZURE_OPENAI" not in body["message"]


# --- the answer page ----------------------------------------------------


def test_the_answer_page_is_locked_down_for_the_sandboxed_frame(logged_in_client, model):
    logged_in_client.post("/ai/1")
    _settle(logged_in_client, 1)

    response = logged_in_client.get("/ai/1/view")

    assert response.status_code == 200
    policy = response.headers["content-security-policy"]
    assert "default-src 'none'" in policy
    assert "img-src data:" in policy
    assert "sandbox" in policy
    assert "script-src" not in policy
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "Ответ: C" in response.text
    assert "<script" not in response.text


def test_the_answer_page_is_404_until_the_model_has_answered(logged_in_client, model):
    assert logged_in_client.get("/ai/1/view").status_code == 404


# --- where the buttons are ----------------------------------------------


def _play_one_session(client) -> None:
    client.post("/session/new", data={"count": "3"})
    for grade in ("correct", "partial", "incorrect"):
        client.post("/exam/grade", data={"grade": grade})


def test_the_exam_screen_has_a_live_ai_button_and_the_popup(logged_in_client, model):
    logged_in_client.post("/session/new", data={"count": "3"})

    page = logged_in_client.get("/exam").text

    assert 'id="ai-btn"' in page and "data-ai-open" in page
    assert 'id="ai-dialog"' in page
    assert "<iframe" in page and "sandbox" in page
    assert 'id="ai-btn" disabled' not in page


def test_the_ai_button_is_disabled_when_nothing_is_configured(logged_in_client):
    _unconfigure()
    try:
        logged_in_client.post("/session/new", data={"count": "3"})
        page = logged_in_client.get("/exam").text
    finally:
        aiassist.install_service(None)

    assert 'id="ai-btn" disabled' in page


def test_every_question_of_the_session_review_has_an_ai_button(logged_in_client, model):
    _play_one_session(logged_in_client)

    page = logged_in_client.get("/results/1").text

    assert page.count("data-ai-open") == 3
    assert 'id="ai-dialog"' in page


# --- the list -----------------------------------------------------------


def _ask(client, question_id: int) -> None:
    client.post(f"/ai/{question_id}")
    assert _settle(client, question_id)["state"] == "done"


def test_the_menu_links_to_the_list(logged_in_client):
    assert 'href="/ai"' in logged_in_client.get("/").text


def test_the_empty_list_explains_itself(logged_in_client, model):
    page = logged_in_client.get("/ai").text

    assert "Пока ни один вопрос не отправлялся" in page
    assert "<table" not in page


def test_the_list_shows_every_checked_question_with_its_description(logged_in_client, model):
    _ask(logged_in_client, 2)
    model.summary, model.answer = "Peering между сетями", "A, D"
    _ask(logged_in_client, 1)

    page = logged_in_client.get("/ai").text

    assert "Проверено вопросов: 2" in page
    assert "токенов всего: 3 000" in page
    assert "Выбор SKU шлюза" in page and "Peering между сетями" in page
    assert ">A, D<" in page
    assert page.count("data-ai-open") == 4  # the row and the link of each of the two questions
    assert "Показать вопрос" in page  # the list's popup can show the picture


def _question_order(page: str) -> list[int]:
    import re

    return [int(n) for n in re.findall(r'class="btn-link ai-open-link"[^>]*>№(\d+)<', page)]


def test_the_list_sorts_by_question_both_ways(logged_in_client, model):
    for question_id in (3, 1, 2):
        _ask(logged_in_client, question_id)

    ascending = logged_in_client.get("/ai?sort=question&dir=asc").text
    descending = logged_in_client.get("/ai?sort=question&dir=desc").text

    assert _question_order(ascending) == [1, 2, 3]
    assert _question_order(descending) == [3, 2, 1]


def test_the_list_sorts_by_date_with_newest_first_by_default(logged_in_client, model):
    for question_id in (3, 1, 2):
        _ask(logged_in_client, question_id)
        time.sleep(1.1)  # the stored time has a one-second resolution

    default = logged_in_client.get("/ai").text
    oldest_first = logged_in_client.get("/ai?sort=date&dir=asc").text

    assert _question_order(default) == [2, 1, 3]
    assert _question_order(oldest_first) == [3, 1, 2]


def test_the_header_links_flip_the_current_sort_and_start_the_other_one(logged_in_client, model):
    _ask(logged_in_client, 1)

    by_date = logged_in_client.get("/ai?sort=date&dir=desc").text
    by_question = logged_in_client.get("/ai?sort=question&dir=asc").text

    # Currently by date, newest first: its header flips to oldest first, the other column starts at its own default.
    assert 'href="/ai?sort=date&amp;dir=asc"' in by_date
    assert 'href="/ai?sort=question&amp;dir=asc"' in by_date
    # Currently by question, ascending: its header flips to descending.
    assert 'href="/ai?sort=question&amp;dir=desc"' in by_question
    assert 'href="/ai?sort=date&amp;dir=desc"' in by_question


def test_garbage_in_the_query_string_falls_back_to_the_default_order(logged_in_client, model):
    _ask(logged_in_client, 1)

    response = logged_in_client.get("/ai?sort=%27%3B+DROP+TABLE&dir=sideways")

    assert response.status_code == 200
    assert _question_order(response.text) == [1]
