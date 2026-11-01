"""
The AI check service: one paid request per question, however it is asked for.

The model is a fake, so nothing here reaches the network. What is verified is the
behaviour that matters for the bill - a stored answer is never asked for again,
simultaneous clicks make one request, a failure is not remembered as an answer.
"""

from __future__ import annotations

import threading
import time

import pytest

from api.modules.aiassist import api as aiassist
from api.modules.aiassist import repo
from api.modules.aiassist.internal._client import AiRequestError, AzureConfig, ModelReply
from api.modules.examsession import api as examsession
from api.modules.shared.api import get_connection

CONFIG = AzureConfig(
    base_url="https://example.openai.azure.com/openai/v1/",
    api_key="test-key-not-a-secret",
    deployment="test-deployment",
)

GOOD_HTML = '<div class="verdict">✅ Ответ: B</div><p>Потому что.</p>'


def _reply(**overrides) -> ModelReply:
    fields = {
        "summary": "Выбор SKU шлюза",
        "answer": "B",
        "html": GOOD_HTML,
        "model": "test-model-2026",
        "response_id": "resp_1",
        "input_tokens": 1100,
        "output_tokens": 560,
    }
    fields.update(overrides)
    return ModelReply(**fields)


class FakeModel:
    """Stands in for the network call; counts calls and can be held up."""

    def __init__(self, reply: ModelReply | Exception | None = None) -> None:
        self.reply = reply or _reply()
        self.calls: list[tuple[int, int]] = []
        self.release = threading.Event()
        self.release.set()
        self._lock = threading.Lock()

    def __call__(self, config: AzureConfig, question_id: int, png: bytes) -> ModelReply:
        with self._lock:
            self.calls.append((question_id, len(png)))
        assert self.release.wait(timeout=10), "test forgot to release the fake model"
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "ai.sqlite"
    conn = get_connection(path)
    aiassist.ensure_schema(conn)
    examsession.ensure_schema(conn)
    conn.close()
    return path


@pytest.fixture()
def png(tmp_path):
    path = tmp_path / "q0001_question.png"
    path.write_bytes(b"\x89PNG fake picture bytes")
    return path


@pytest.fixture()
def make_service(db_path):
    created: list[aiassist.AiService] = []

    def factory(model: FakeModel, **kwargs) -> aiassist.AiService:
        kwargs.setdefault("load_config", lambda: CONFIG)
        service = aiassist.AiService(db_path=lambda: db_path, ask=model, **kwargs)
        created.append(service)
        return service

    yield factory
    for service in created:
        service.shutdown()


def _wait_until_settled(service: aiassist.AiService, question_id: int) -> aiassist.AiStatus:
    """Poll like the page does, until the request is no longer running."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = service.status(question_id)
        if status.state != aiassist.STATE_RUNNING:
            return status
        time.sleep(0.01)
    raise AssertionError("the request never finished")


def _stored(db_path, question_id: int):
    conn = get_connection(db_path)
    try:
        return aiassist.get_answer(conn, question_id)
    finally:
        conn.close()


# --- the paid request happens once -------------------------------------


def test_first_check_asks_the_model_once_and_stores_everything(make_service, db_path, png):
    model = FakeModel()
    service = make_service(model)

    started = service.start(1, png)
    assert started.state == aiassist.STATE_RUNNING
    done = _wait_until_settled(service, 1)

    assert done.state == aiassist.STATE_DONE
    assert model.calls == [(1, png.stat().st_size)]
    stored = _stored(db_path, 1)
    assert stored is not None
    assert stored.summary == "Выбор SKU шлюза"
    assert stored.answer == "B"
    assert "Ответ: B" in stored.html
    assert stored.model == "test-model-2026"
    assert stored.effort == "high"
    assert stored.prompt_version >= 1
    assert (stored.input_tokens, stored.output_tokens) == (1100, 560)
    assert stored.created_at


def test_second_click_is_served_from_the_database_without_a_request(make_service, png):
    model = FakeModel()
    service = make_service(model)
    service.start(1, png)
    _wait_until_settled(service, 1)

    again = service.start(1, png)

    assert again.state == aiassist.STATE_DONE
    assert again.answer is not None and again.answer.answer == "B"
    assert len(model.calls) == 1


def test_a_stored_answer_survives_a_restart(make_service, png):
    first = make_service(FakeModel())
    first.start(1, png)
    _wait_until_settled(first, 1)
    first.shutdown()

    model = FakeModel()
    after_restart = make_service(model)
    status = after_restart.start(1, png)

    assert status.state == aiassist.STATE_DONE
    assert model.calls == []


def test_simultaneous_clicks_make_one_request(make_service, png):
    model = FakeModel()
    model.release.clear()  # hold the model so every click lands while it works
    service = make_service(model)

    states: list[str] = []
    barrier = threading.Barrier(5)

    def click() -> None:
        barrier.wait()
        states.append(service.start(1, png).state)

    threads = [threading.Thread(target=click) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    model.release.set()
    _wait_until_settled(service, 1)

    assert states == [aiassist.STATE_RUNNING] * 5
    assert len(model.calls) == 1


def test_status_is_running_with_elapsed_time_while_the_model_works(make_service, png):
    model = FakeModel()
    model.release.clear()
    now = [100.0]
    service = make_service(model, clock=lambda: now[0])

    service.start(1, png)
    now[0] = 142.5
    status = service.status(1)
    model.release.set()
    _wait_until_settled(service, 1)

    assert status.state == aiassist.STATE_RUNNING
    assert status.elapsed_seconds == pytest.approx(42.5)


def test_different_questions_are_independent(make_service, png):
    model = FakeModel()
    service = make_service(model)

    service.start(1, png)
    service.start(2, png)
    _wait_until_settled(service, 1)
    _wait_until_settled(service, 2)

    assert sorted(question for question, _ in model.calls) == [1, 2]


# --- failures are not answers ------------------------------------------


def test_failure_stores_nothing_and_can_be_retried(make_service, db_path, png):
    model = FakeModel(AiRequestError("Модель не ответила вовремя."))
    service = make_service(model)

    service.start(1, png)
    failed = _wait_until_settled(service, 1)

    assert failed.state == aiassist.STATE_ERROR
    assert failed.message == "Модель не ответила вовремя."
    assert _stored(db_path, 1) is None

    model.reply = _reply()
    retried = service.start(1, png)
    assert retried.state == aiassist.STATE_RUNNING
    assert _wait_until_settled(service, 1).state == aiassist.STATE_DONE
    assert len(model.calls) == 2


def test_an_unexpected_crash_is_reported_without_leaking_its_text(make_service, db_path, png):
    model = FakeModel(RuntimeError("secret internals: key=test-key-not-a-secret"))
    service = make_service(model)

    service.start(1, png)
    failed = _wait_until_settled(service, 1)

    assert failed.state == aiassist.STATE_ERROR
    assert "secret internals" not in failed.message
    assert "test-key-not-a-secret" not in failed.message
    assert _stored(db_path, 1) is None


def test_a_missing_picture_is_a_clear_failure(make_service, tmp_path):
    model = FakeModel()
    service = make_service(model)

    service.start(1, tmp_path / "does-not-exist.png")
    failed = _wait_until_settled(service, 1)

    assert failed.state == aiassist.STATE_ERROR
    assert model.calls == []


# --- configuration -----------------------------------------------------


def _unconfigured():
    raise aiassist.AiNotConfigured("Not set: AZURE_OPENAI_API_KEY")


def test_without_settings_nothing_is_started(make_service, png):
    model = FakeModel()
    service = make_service(model, load_config=_unconfigured)

    with pytest.raises(aiassist.AiNotConfigured):
        service.start(1, png)

    assert not service.is_configured()
    assert service.configuration_error() == "Not set: AZURE_OPENAI_API_KEY"
    assert service.status(1).state == aiassist.STATE_IDLE
    assert model.calls == []


def test_a_stored_answer_is_still_shown_after_the_settings_are_removed(make_service, png):
    configured = make_service(FakeModel())
    configured.start(1, png)
    _wait_until_settled(configured, 1)

    unconfigured = make_service(FakeModel(), load_config=_unconfigured)

    assert unconfigured.start(1, png).state == aiassist.STATE_DONE


# --- what is stored ----------------------------------------------------


def test_model_html_is_sanitised_before_it_is_stored(make_service, db_path, png):
    hostile = '<div class="verdict" onclick="x()">ok</div><script>alert(1)</script><img src="http://evil/x">'
    service = make_service(FakeModel(_reply(html=hostile, summary="<b>Тема</b><script>x</script>")))

    service.start(1, png)
    _wait_until_settled(service, 1)

    stored = _stored(db_path, 1)
    assert stored is not None
    assert "<script" not in stored.html and "onclick" not in stored.html and "<img" not in stored.html
    assert 'class="verdict"' in stored.html
    assert stored.summary == "Тема"


def test_an_empty_explanation_falls_back_to_the_short_answer(make_service, db_path, png):
    service = make_service(FakeModel(_reply(html="<script>1</script>", answer="A & D")))

    service.start(1, png)
    _wait_until_settled(service, 1)

    stored = _stored(db_path, 1)
    assert stored is not None
    assert "A &amp; D" in stored.html


def test_nothing_usable_is_an_error_and_is_not_stored(make_service, db_path, png):
    service = make_service(FakeModel(_reply(html="", answer="", summary="")))

    service.start(1, png)
    failed = _wait_until_settled(service, 1)

    assert failed.state == aiassist.STATE_ERROR
    assert _stored(db_path, 1) is None


def test_summary_and_answer_are_cut_to_their_limits(make_service, db_path, png):
    service = make_service(FakeModel(_reply(summary="я" * 500, answer="B" * 500)))

    service.start(1, png)
    _wait_until_settled(service, 1)

    stored = _stored(db_path, 1)
    assert stored is not None
    assert len(stored.summary) == 140 and len(stored.answer) == 120


# --- the table ---------------------------------------------------------


def _answer(question_id: int, created_at: str, **overrides) -> aiassist.AiAnswer:
    fields = {
        "question_id": question_id,
        "html": "<p>x</p>",
        "summary": f"summary {question_id}",
        "answer": "A",
        "model": "m",
        "effort": "high",
        "prompt_version": 1,
        "response_id": "",
        "input_tokens": 10,
        "output_tokens": 20,
        "created_at": created_at,
    }
    fields.update(overrides)
    return aiassist.AiAnswer(**fields)


def test_migration_is_idempotent(db_path):
    conn = get_connection(db_path)
    try:
        assert aiassist.ensure_schema(conn) == aiassist.ensure_schema(conn) == 1
    finally:
        conn.close()


def test_save_never_replaces_an_answer_already_paid_for(db_path):
    conn = get_connection(db_path)
    try:
        repo.save(conn, _answer(7, "2026-01-01T10:00:00+00:00", answer="first"))
        repo.save(conn, _answer(7, "2026-02-02T10:00:00+00:00", answer="second"))

        stored = aiassist.get_answer(conn, 7)
        assert stored is not None and stored.answer == "first"
    finally:
        conn.close()


def test_list_is_sorted_by_date_and_by_question_both_ways(db_path):
    conn = get_connection(db_path)
    try:
        repo.save(conn, _answer(30, "2026-03-01T10:00:00+00:00"))
        repo.save(conn, _answer(5, "2026-03-03T10:00:00+00:00"))
        repo.save(conn, _answer(100, "2026-03-02T10:00:00+00:00"))

        def order(sort: str, direction: str) -> list[int]:
            return [a.question_id for a in aiassist.list_answers(conn, sort, direction)]

        assert order(aiassist.SORT_DATE, aiassist.DIRECTION_DESC) == [5, 100, 30]
        assert order(aiassist.SORT_DATE, aiassist.DIRECTION_ASC) == [30, 100, 5]
        assert order(aiassist.SORT_QUESTION, aiassist.DIRECTION_ASC) == [5, 30, 100]
        assert order(aiassist.SORT_QUESTION, aiassist.DIRECTION_DESC) == [100, 30, 5]
        # Garbage from a query string falls back to newest first instead of failing.
        assert order("; DROP TABLE ai_answers", "sideways") == [5, 100, 30]
    finally:
        conn.close()


def test_resetting_statistics_keeps_the_paid_answers(db_path):
    conn = get_connection(db_path)
    try:
        repo.save(conn, _answer(7, "2026-01-01T10:00:00+00:00"))

        examsession.reset_all_sessions(conn)
        conn.commit()

        assert aiassist.get_answer(conn, 7) is not None
    finally:
        conn.close()
