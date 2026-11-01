"""
Public API of the ``aiassist`` module: asking a model about a question picture.

A question in this application is only a PNG. This module sends that picture to
a model deployed in Azure OpenAI and keeps what comes back - the model's answer,
a one-line description of the question and a formatted explanation - so that each
question is asked about at most once. Every request is paid for.

The module does not know about the question catalog: the caller passes the path
of the picture. Requests run on background threads; the caller starts one with
:func:`start` and polls :func:`status`.

Example:
    >>> from api.modules.aiassist import api as aiassist
    >>> if aiassist.is_configured():
    ...     state = aiassist.start(52, image_path).state   # "running" or "done"
    ...     state = aiassist.status(52).state              # poll until "done" / "error"
"""

from __future__ import annotations

from pathlib import Path

from api.modules.aiassist import repo
from api.modules.aiassist.domain import (
    DIRECTION_ASC,
    DIRECTION_DESC,
    SORT_DATE,
    SORT_QUESTION,
    STATE_DONE,
    STATE_ERROR,
    STATE_IDLE,
    STATE_RUNNING,
    AiAnswer,
    AiNotConfigured,
    AiStatus,
)
from api.modules.aiassist.internal._sanitize import ALLOWED_CLASSES
from api.modules.aiassist.services import AiService

__all__ = [
    "AiAnswer",
    "AiNotConfigured",
    "AiService",
    "AiStatus",
    "ALLOWED_CLASSES",
    "DIRECTION_ASC",
    "DIRECTION_DESC",
    "SORT_DATE",
    "SORT_QUESTION",
    "STATE_DONE",
    "STATE_ERROR",
    "STATE_IDLE",
    "STATE_RUNNING",
    "ensure_schema",
    "get_answer",
    "list_answers",
    "install_service",
    "is_configured",
    "configuration_error",
    "deployment_name",
    "start",
    "status",
    "shutdown",
]

ensure_schema = repo.ensure_schema

#: The stored answer of a question (``None`` if it was never asked), and every
#: stored answer in the order asked for. Both take an open connection.
get_answer = repo.get
list_answers = repo.list_all

_service: AiService | None = None


def _get_service() -> AiService:
    global _service
    if _service is None:
        _service = AiService()
    return _service


def install_service(service: AiService | None) -> None:
    """
    Replace the process-wide service, stopping the previous one.

    The application calls this once at startup so every run begins with an empty
    registry; tests call it to put a service with a fake model in place.
    """
    global _service
    previous, _service = _service, service
    if previous is not None and previous is not service:
        previous.shutdown()


def shutdown() -> None:
    """Stop the process-wide service, if one was created."""
    install_service(None)


def is_configured() -> bool:
    """Whether the Azure OpenAI settings are present and valid."""
    return _get_service().is_configured()


def configuration_error() -> str | None:
    """What is wrong with the settings, or ``None``. Never contains the key."""
    return _get_service().configuration_error()


def deployment_name() -> str:
    """Name of the configured deployment, or ``""`` when not configured."""
    return _get_service().deployment_name()


def start(question_id: int, image_path: Path) -> AiStatus:
    """
    Check the question unless it already has been, and report where it stands.

    Idempotent: a stored answer is returned; a request already running is
    joined; otherwise one request is started in the background.

    Raises:
        AiNotConfigured: If a new request is needed and the settings are missing.
    """
    return _get_service().start(question_id, image_path)


def status(question_id: int) -> AiStatus:
    """Where the question's check stands (``idle``, ``running``, ``done`` or ``error``)."""
    return _get_service().status(question_id)
