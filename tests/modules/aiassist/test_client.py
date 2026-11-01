"""
The Azure OpenAI client: settings, the shape of the request, and failures.

No test here reaches the network: the SDK client is replaced by a fake that
records what it was asked.
"""

from __future__ import annotations

import base64
import logging
from types import SimpleNamespace

import pytest

from api.modules.aiassist.domain import AiNotConfigured
from api.modules.aiassist.internal import _client, _prompt

KEY = "super-secret-key-1234567890"
ENDPOINT = "https://example.openai.azure.com/openai/v1"
ENV = {
    "AZURE_OPENAI_ENDPOINT": ENDPOINT,
    "AZURE_OPENAI_API_KEY": KEY,
    "AZURE_OPENAI_DEPLOYMENT": "my-deployment",
}
PNG = b"\x89PNG\r\n\x1a\nfake"


# --- settings ----------------------------------------------------------


@pytest.mark.parametrize(
    "given",
    [
        "https://example.openai.azure.com/openai/v1",
        "https://example.openai.azure.com/openai/v1/",
        "  https://EXAMPLE.openai.azure.com/openai/v1  ",
        "https://example.openai.azure.com:443/openai/v1",
    ],
)
def test_endpoint_is_normalised_for_the_sdk(given):
    assert _client.normalize_endpoint(given).endswith("/openai/v1/")
    assert _client.normalize_endpoint(given).startswith("https://")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "http://example.openai.azure.com/openai/v1",
        "https://example.com/openai/v1",
        "https://example.openai.azure.com.evil.com/openai/v1",
        "https://example.openai.azure.com:8443/openai/v1",
        "https://example.openai.azure.com/openai/v1?api-version=1",
        "https://user:pw@example.openai.azure.com/openai/v1",
        "https://example.openai.azure.com/",
        "https://example.openai.azure.com/openai/deployments/x",
    ],
)
def test_a_wrong_endpoint_is_refused_so_the_key_cannot_be_sent_elsewhere(bad):
    with pytest.raises(AiNotConfigured):
        _client.normalize_endpoint(bad)


def test_config_is_read_from_the_environment():
    config = _client.load_config(ENV)

    assert config.deployment == "my-deployment"
    assert config.base_url == "https://example.openai.azure.com/openai/v1/"
    assert config.api_key == KEY


def test_missing_settings_are_named_but_never_the_key():
    with pytest.raises(AiNotConfigured) as caught:
        _client.load_config({"AZURE_OPENAI_API_KEY": KEY})

    message = str(caught.value)
    assert "AZURE_OPENAI_ENDPOINT" in message and "AZURE_OPENAI_DEPLOYMENT" in message
    assert KEY not in message


def test_the_deployment_has_no_default():
    env = {k: v for k, v in ENV.items() if k != "AZURE_OPENAI_DEPLOYMENT"}
    with pytest.raises(AiNotConfigured):
        _client.load_config(env)


def test_the_key_is_not_in_the_repr_of_the_config():
    assert KEY not in repr(_client.load_config(ENV))


# --- the request -------------------------------------------------------


def test_request_has_the_agreed_shape():
    config = _client.load_config(ENV)

    request = _client.build_request(config, PNG)

    assert request["model"] == "my-deployment"
    assert request["reasoning"] == {"effort": "high"}
    assert request["store"] is False
    assert request["max_output_tokens"] == _prompt.MAX_OUTPUT_TOKENS
    assert request["instructions"] == _prompt.INSTRUCTIONS

    (message,) = request["input"]
    text_part, image_part = message["content"]
    assert text_part["type"] == "input_text"
    assert image_part["type"] == "input_image"
    assert image_part["detail"] == "high"
    prefix = "data:image/png;base64,"
    assert image_part["image_url"].startswith(prefix)
    assert base64.b64decode(image_part["image_url"][len(prefix):]) == PNG

    fmt = request["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True
    assert fmt["schema"]["required"] == ["summary", "answer", "html"]
    assert fmt["schema"]["additionalProperties"] is False


def test_only_the_question_picture_is_sent():
    request = _client.build_request(_client.load_config(ENV), PNG)

    parts = request["input"][0]["content"]
    assert [part["type"] for part in parts] == ["input_text", "input_image"]


def test_the_instructions_tell_the_model_about_the_dump_errors_and_the_classes():
    assert "AZ-700" in _prompt.INSTRUCTIONS
    assert "нередко указаны неверно" in _prompt.INSTRUCTIONS
    for name in ("verdict", "opt right", "opt wrong", "tip", "note", "warn"):
        assert name in _prompt.INSTRUCTIONS


# --- the reply text ----------------------------------------------------


def test_json_reply_is_split_into_its_fields():
    summary, answer, html = _client.parse_reply_text('{"summary": "S", "answer": "B", "html": "<p>h</p>"}')

    assert (summary, answer, html) == ("S", "B", "<p>h</p>")


@pytest.mark.parametrize("text", ["just prose, not JSON", "[1, 2, 3]", '"a string"'])
def test_a_reply_that_is_not_the_agreed_json_is_kept_as_the_explanation(text):
    assert _client.parse_reply_text(text) == ("", "", text)


# --- the call ----------------------------------------------------------


class FakeResponses:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def _client_with(responses: FakeResponses):
    return SimpleNamespace(responses=responses)


def _response(text: str, **overrides):
    fields = {
        "output_text": text,
        "id": "resp_42",
        "status": "completed",
        "model": "gpt-served-model",
        "usage": SimpleNamespace(input_tokens=1114, output_tokens=558),
        "incomplete_details": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


JSON_TEXT = '{"summary": "Тема", "answer": "C", "html": "<p>разбор</p>"}'


def test_a_successful_call_returns_the_reply_with_its_cost():
    responses = FakeResponses(_response(JSON_TEXT))

    reply = _client.ask_model(_client.load_config(ENV), 52, PNG, client=_client_with(responses))

    assert (reply.summary, reply.answer, reply.html) == ("Тема", "C", "<p>разбор</p>")
    assert reply.model == "gpt-served-model" and reply.response_id == "resp_42"
    assert (reply.input_tokens, reply.output_tokens) == (1114, 558)
    (sent,) = responses.requests
    assert sent["reasoning"] == {"effort": "high"} and sent["store"] is False


class _ApiError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@pytest.mark.parametrize(
    ("status", "fragment"),
    [(401, "ключ"), (403, "развёртыванию"), (404, "развёртыванию"), (429, "429"), (400, "отклонил запрос"), (503, "недоступен")],
)
def test_failures_become_plain_messages_without_the_vendor_text_or_the_key(status, fragment):
    error = _ApiError(f"Error code: {status} - bad key {KEY} for deployment x", status_code=status)
    responses = FakeResponses(error=error)

    with pytest.raises(_client.AiRequestError) as caught:
        _client.ask_model(_client.load_config(ENV), 1, PNG, client=_client_with(responses))

    message = str(caught.value)
    assert fragment in message
    assert KEY not in message and "bad key" not in message


def test_a_timeout_and_a_dropped_connection_have_their_own_messages():
    class APITimeoutError(Exception):
        pass

    class APIConnectionError(Exception):
        pass

    config = _client.load_config(ENV)
    assert "не ответила" in _client.describe_error(APITimeoutError("t"), config)
    assert "связаться" in _client.describe_error(APIConnectionError("c"), config)


def test_the_log_has_no_key_and_no_picture(caplog):
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = Capture(level=logging.DEBUG)
    _client.logger.addHandler(handler)
    previous_level = _client.logger.level
    _client.logger.setLevel(logging.DEBUG)
    try:
        ok = FakeResponses(_response(JSON_TEXT))
        _client.ask_model(_client.load_config(ENV), 5, PNG, client=_client_with(ok))
        failing = FakeResponses(error=_ApiError(f"401 {KEY}", status_code=401))
        with pytest.raises(_client.AiRequestError):
            _client.ask_model(_client.load_config(ENV), 5, PNG, client=_client_with(failing))
    finally:
        _client.logger.removeHandler(handler)
        _client.logger.setLevel(previous_level)

    joined = "\n".join(records)
    assert "question=5" in joined and "input_tokens=1114" in joined
    assert KEY not in joined
    assert base64.b64encode(PNG).decode() not in joined


@pytest.mark.parametrize(
    ("response", "fragment"),
    [
        (_response(""), "пустой"),
        (_response("   "), "пустой"),
        (_response('{"summary": "tru', status="incomplete"), "неполный"),
        (
            _response("", status="incomplete", incomplete_details=SimpleNamespace(reason="max_output_tokens")),
            "лимит",
        ),
    ],
)
def test_an_empty_or_cut_off_reply_is_a_failure_not_an_answer(response, fragment):
    responses = FakeResponses(response)

    with pytest.raises(_client.AiRequestError) as caught:
        _client.ask_model(_client.load_config(ENV), 1, PNG, client=_client_with(responses))

    assert fragment in str(caught.value)


def test_redact_removes_keys_and_bearer_tokens():
    config = _client.load_config(ENV)
    text = f"failed with {KEY}; Authorization: Bearer abc.def.ghi and sk-ABCDEFGHIJKLMNOP1234"

    cleaned = _client.redact(text, config)

    assert KEY not in cleaned and "abc.def.ghi" not in cleaned and "sk-ABCDEFGHIJKLMNOP1234" not in cleaned
