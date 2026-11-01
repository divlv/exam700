"""
The one place that talks to Azure OpenAI.

Settings come from three environment variables, so the deployment name and the
key live in the cluster's Secret and never in this public repository:

``AZURE_OPENAI_ENDPOINT``
    ``https://<resource>.openai.azure.com/openai/v1``
``AZURE_OPENAI_API_KEY``
    The resource key.
``AZURE_OPENAI_DEPLOYMENT``
    Name of the model deployment (it is what the Responses API calls ``model``).
    There is no default on purpose.

The request is a single Responses API call with the picture of the question and
a strict JSON schema for the reply. Nothing here logs the key, an authorization
header or the base64 picture; what is logged is enough to tell requests apart
and to see what they cost.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from openai import OpenAI

from api.modules.aiassist.domain import AiNotConfigured
from api.modules.aiassist.internal import _prompt
from api.modules.shared.api import get_logger

logger = get_logger(__name__)

ENV_ENDPOINT = "AZURE_OPENAI_ENDPOINT"
ENV_API_KEY = "AZURE_OPENAI_API_KEY"
ENV_DEPLOYMENT = "AZURE_OPENAI_DEPLOYMENT"

#: A request with high reasoning effort can think for minutes.
REQUEST_TIMEOUT_SECONDS = 600.0

#: Retries the SDK does itself, only for rate limits, 5xx and dropped connections.
MAX_RETRIES = 2

_REDACTED = "<redacted>"


class AiRequestError(RuntimeError):
    """A request failed. The message is safe to show to the user: it has no secrets."""


@dataclass(frozen=True)
class AzureConfig:
    """Validated settings. Its ``repr`` omits the key, so it is safe to log."""

    base_url: str
    api_key: str
    deployment: str

    def __repr__(self) -> str:
        return f"AzureConfig(base_url={self.base_url!r}, deployment={self.deployment!r})"


@dataclass(frozen=True)
class ModelReply:
    """What came back from one request, already cleaned up."""

    summary: str
    answer: str
    html: str
    model: str
    response_id: str
    input_tokens: int
    output_tokens: int


def normalize_endpoint(endpoint: str) -> str:
    """
    Check the endpoint and bring it to the form the SDK wants as ``base_url``.

    Only an HTTPS ``*.openai.azure.com`` host with the ``/openai/v1`` path is
    accepted, so a mistyped or hostile value can never send the key elsewhere.

    Raises:
        AiNotConfigured: If the value is empty or does not fit.
    """
    value = endpoint.strip()
    if not value:
        raise AiNotConfigured(f"{ENV_ENDPOINT} is empty.")
    parsed = urlsplit(value)
    if parsed.scheme.casefold() != "https":
        raise AiNotConfigured(f"{ENV_ENDPOINT} must use HTTPS.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AiNotConfigured(f"{ENV_ENDPOINT} must not contain credentials, a query or a fragment.")
    hostname = (parsed.hostname or "").casefold()
    if not hostname.endswith(".openai.azure.com"):
        raise AiNotConfigured(f"{ENV_ENDPOINT} must be an openai.azure.com host.")
    if parsed.port not in (None, 443):
        raise AiNotConfigured(f"{ENV_ENDPOINT} must use the default HTTPS port.")
    if parsed.path.rstrip("/").casefold() != "/openai/v1":
        raise AiNotConfigured(f"{ENV_ENDPOINT} path must be /openai/v1.")
    return f"https://{parsed.netloc}/openai/v1/"


def load_config(environ: Mapping[str, str] | None = None) -> AzureConfig:
    """
    Read and validate the settings.

    Raises:
        AiNotConfigured: Naming the variable that is missing or malformed. The
            message never contains the key.
    """
    env = os.environ if environ is None else environ
    endpoint = env.get(ENV_ENDPOINT, "").strip()
    api_key = env.get(ENV_API_KEY, "").strip()
    deployment = env.get(ENV_DEPLOYMENT, "").strip()
    missing = [
        name
        for name, value in ((ENV_ENDPOINT, endpoint), (ENV_API_KEY, api_key), (ENV_DEPLOYMENT, deployment))
        if not value
    ]
    if missing:
        raise AiNotConfigured("Not set: " + ", ".join(missing))
    return AzureConfig(base_url=normalize_endpoint(endpoint), api_key=api_key, deployment=deployment)


def redact(text: str, config: AzureConfig | None = None) -> str:
    """Remove the key, bearer tokens and ``sk-`` style keys from a message."""
    secret = config.api_key if config else os.environ.get(ENV_API_KEY, "")
    if secret:
        text = text.replace(secret, _REDACTED)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", _REDACTED, text)
    return re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", rf"\1{_REDACTED}", text)


def describe_error(error: BaseException, config: AzureConfig | None = None) -> str:
    """
    Turn an SDK failure into a short explanation for the user.

    The raw message goes to the log (redacted); the user gets a plain-language
    cause, because a vendor error body is neither readable nor guaranteed free of
    internal details.
    """
    status = getattr(error, "status_code", None)
    name = error.__class__.__name__
    if name == "APITimeoutError":
        return "Модель не ответила вовремя. Попробуйте ещё раз."
    if name == "APIConnectionError":
        return "Не удалось связаться с Azure OpenAI. Проверьте сеть и попробуйте ещё раз."
    if status == 401:
        return "Azure OpenAI отклонил ключ доступа (401). Проверьте AZURE_OPENAI_API_KEY."
    if status in (403, 404):
        return (
            f"Нет доступа к развёртыванию модели ({status}). "
            "Проверьте AZURE_OPENAI_DEPLOYMENT и права ключа."
        )
    if status == 429:
        return "Azure OpenAI ограничил частоту запросов или квоту (429). Попробуйте позже."
    if status in (400, 422):
        return f"Azure OpenAI отклонил запрос ({status}). Подробности в журнале сервера."
    if isinstance(status, int) and status >= 500:
        return f"Сервис Azure OpenAI временно недоступен ({status}). Попробуйте позже."
    return "Не удалось получить ответ модели. Подробности в журнале сервера."


def build_client(config: AzureConfig) -> OpenAI:
    """Create the SDK client for the validated settings."""
    return OpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=MAX_RETRIES,
    )


def build_request(config: AzureConfig, png: bytes) -> dict[str, Any]:
    """
    Assemble the keyword arguments of ``responses.create``.

    Separate from the call so that what is sent can be checked without a network.
    ``store=False`` keeps the picture from being retained by the service.
    """
    data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    return {
        "model": config.deployment,
        "instructions": _prompt.INSTRUCTIONS,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": _prompt.USER_TEXT},
                    {"type": "input_image", "image_url": data_url, "detail": _prompt.IMAGE_DETAIL},
                ],
            }
        ],
        "reasoning": {"effort": _prompt.EFFORT},
        "text": {
            "format": {
                "type": "json_schema",
                "name": _prompt.RESPONSE_SCHEMA_NAME,
                "strict": True,
                "schema": _prompt.RESPONSE_SCHEMA,
            }
        },
        "max_output_tokens": _prompt.MAX_OUTPUT_TOKENS,
        "store": False,
    }


def parse_reply_text(text: str) -> tuple[str, str, str]:
    """
    Split the model's text into ``(summary, answer, html)``.

    The schema is strict, so the text is normally JSON. If it is not, the whole
    text is taken as the explanation rather than thrown away: it was paid for.
    """
    try:
        data = json.loads(text)
    except ValueError:
        return "", "", text
    if not isinstance(data, dict):
        return "", "", text
    return (
        str(data.get("summary") or ""),
        str(data.get("answer") or ""),
        str(data.get("html") or ""),
    )


def _usage(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    return int(getattr(usage, "input_tokens", 0) or 0), int(getattr(usage, "output_tokens", 0) or 0)


def ask_model(
    config: AzureConfig,
    question_id: int,
    png: bytes,
    client: OpenAI | None = None,
) -> ModelReply:
    """
    Send one question picture to the model and return its reply.

    Args:
        config: Validated settings.
        question_id: Only for the log.
        png: The question picture.
        client: Replaces the SDK client; tests pass a fake.

    Raises:
        AiRequestError: With a message safe for the user, if the request failed
            or the model returned nothing.
    """
    request = build_request(config, png)
    sha = hashlib.sha256(png).hexdigest()[:16]
    logger.info(
        "ai request: question=%d model=%s effort=%s detail=%s image_bytes=%d image_sha256=%s prompt_version=%d",
        question_id,
        config.deployment,
        _prompt.EFFORT,
        _prompt.IMAGE_DETAIL,
        len(png),
        sha,
        _prompt.PROMPT_VERSION,
    )
    started = time.monotonic()
    try:
        response = (client or build_client(config)).responses.create(**request)
    except Exception as error:  # noqa: BLE001 - every SDK failure is reported the same way
        elapsed = time.monotonic() - started
        logger.error(
            "ai request failed: question=%d after=%.1fs error=%s status=%s detail=%s",
            question_id,
            elapsed,
            error.__class__.__name__,
            getattr(error, "status_code", None),
            redact(str(error), config)[:1000],
        )
        raise AiRequestError(describe_error(error, config)) from error

    elapsed = time.monotonic() - started
    text = (getattr(response, "output_text", "") or "").strip()
    input_tokens, output_tokens = _usage(response)
    response_id = str(getattr(response, "id", "") or "")
    logger.info(
        "ai response: question=%d after=%.1fs status=%s response_id=%s input_tokens=%d output_tokens=%d",
        question_id,
        elapsed,
        getattr(response, "status", ""),
        response_id,
        input_tokens,
        output_tokens,
    )
    logger.debug("ai reply text: question=%d text=%s", question_id, text)

    status = getattr(response, "status", "")
    if status == "incomplete" or not text:
        # A cut-off reply is a broken JSON document; storing it would keep the
        # damage for good, so it is reported as a failure and nothing is saved.
        incomplete = getattr(response, "incomplete_details", None)
        reason = getattr(incomplete, "reason", "") if incomplete else ""
        logger.error(
            "ai reply unusable: question=%d status=%s reason=%s empty=%s",
            question_id,
            status,
            reason,
            not text,
        )
        if reason == "max_output_tokens":
            raise AiRequestError("Модель не уложилась в лимит ответа. Попробуйте ещё раз.")
        raise AiRequestError("Модель вернула неполный или пустой ответ. Попробуйте ещё раз.")

    summary, answer, html = parse_reply_text(text)
    return ModelReply(
        summary=summary,
        answer=answer,
        html=html,
        model=str(getattr(response, "model", "") or config.deployment),
        response_id=response_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
