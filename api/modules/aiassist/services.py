"""
Running the checks: one paid request per question, in the background.

A request to the model with high reasoning effort can take minutes, which is
longer than a phone keeps a tab alive and longer than a proxy waits. So the
request runs on a worker thread of this process and the page polls for the
result. Closing the popup, losing the connection or opening the same question in
a second tab never starts a second request: the service knows which questions
are being worked on, and an answer, once stored, is simply read back.

All state that must survive lives in SQLite (the answer). The registry of running
jobs lives in memory, which is enough: the server is a single process, and a job
interrupted by a restart left nothing behind to clean up.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from html import escape
from pathlib import Path

from api.modules.aiassist import repo
from api.modules.aiassist.domain import (
    STATE_DONE,
    STATE_ERROR,
    STATE_IDLE,
    STATE_RUNNING,
    AiAnswer,
    AiNotConfigured,
    AiStatus,
)
from api.modules.aiassist.internal import _client, _prompt, _sanitize
from api.modules.aiassist.internal._client import AiRequestError, AzureConfig, ModelReply
from api.modules.shared import api as shared

logger = shared.get_logger(__name__)

#: Longest stored lengths of the two short fields, in characters.
SUMMARY_LIMIT = 140
ANSWER_LIMIT = 120

#: How many questions may be with the model at once. Two is enough for one
#: person (a second tab, a click in the review) and keeps the bill predictable.
MAX_WORKERS = 2

AskFn = Callable[[AzureConfig, int, bytes], ModelReply]
ConfigFn = Callable[[], AzureConfig]


@dataclass
class _Job:
    """A request in flight, or the last failed one."""

    state: str
    started_at: float
    message: str = ""


class AiService:
    """
    Starts, tracks and stores the model's checks.

    Args:
        db_path: Returns the SQLite file the workers open their own connections
            to. Called for each connection, so it follows ``shared.DB_PATH``.
        load_config: Reads and validates the settings; raises
            :class:`AiNotConfigured`.
        ask: Sends one picture to the model. Tests pass a fake here, so no
            test ever reaches the network.
        max_workers: Questions processed at the same time.
        clock: Monotonic clock, for the elapsed time of a running request.
    """

    def __init__(
        self,
        *,
        db_path: Callable[[], Path] | None = None,
        load_config: ConfigFn = _client.load_config,
        ask: AskFn = _client.ask_model,
        max_workers: int = MAX_WORKERS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._db_path = db_path or (lambda: shared.DB_PATH)
        self._load_config = load_config
        self._ask = ask
        self._clock = clock
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ai")
        self._lock = threading.Lock()
        self._jobs: dict[int, _Job] = {}

    # --- configuration -------------------------------------------------

    def configuration_error(self) -> str | None:
        """Why the checks are unavailable, or ``None`` when they are ready."""
        try:
            self._load_config()
        except AiNotConfigured as error:
            return str(error)
        return None

    def is_configured(self) -> bool:
        """Whether a new request could be sent."""
        return self.configuration_error() is None

    def deployment_name(self) -> str:
        """Name of the configured model deployment, or ``""`` when not configured."""
        try:
            return self._load_config().deployment
        except AiNotConfigured:
            return ""

    # --- requests ------------------------------------------------------

    def start(self, question_id: int, image_path: Path) -> AiStatus:
        """
        Make sure the question is being checked, without ever paying twice.

        In order: a stored answer is returned as it is; a request already in
        flight is joined; otherwise a new one is started. The whole decision is
        made under one lock, so two clicks (or two tabs) at the same moment lead
        to exactly one request.

        Raises:
            AiNotConfigured: Only when a new request would be needed. A stored
                answer is served even if the settings have since been removed.
        """
        with self._lock:
            stored = self._stored(question_id)
            if stored is not None:
                self._jobs.pop(question_id, None)
                return AiStatus(STATE_DONE, answer=stored)

            job = self._jobs.get(question_id)
            if job is not None and job.state == STATE_RUNNING:
                return self._running_status(job)

            config = self._load_config()  # raises AiNotConfigured, before any job exists
            job = _Job(state=STATE_RUNNING, started_at=self._clock())
            self._jobs[question_id] = job
            self._executor.submit(self._run, question_id, image_path, config)
            logger.info("ai job queued: question=%d model=%s", question_id, config.deployment)
            return self._running_status(job)

    def status(self, question_id: int) -> AiStatus:
        """Where the question's check stands. Never starts anything."""
        with self._lock:
            stored = self._stored(question_id)
            if stored is not None:
                return AiStatus(STATE_DONE, answer=stored)
            job = self._jobs.get(question_id)
            if job is None:
                return AiStatus(STATE_IDLE)
            if job.state == STATE_RUNNING:
                return self._running_status(job)
            return AiStatus(STATE_ERROR, message=job.message)

    def shutdown(self) -> None:
        """Stop accepting work. A request already with the model is not waited for."""
        self._executor.shutdown(wait=False, cancel_futures=True)

    # --- internals -----------------------------------------------------

    def _running_status(self, job: _Job) -> AiStatus:
        return AiStatus(STATE_RUNNING, elapsed_seconds=max(0.0, self._clock() - job.started_at))

    def _stored(self, question_id: int) -> AiAnswer | None:
        conn = shared.get_connection(self._db_path())
        try:
            return repo.get(conn, question_id)
        finally:
            conn.close()

    def _run(self, question_id: int, image_path: Path, config: AzureConfig) -> None:
        """Worker body: ask, clean, store. Whatever happens, the job ends in a state."""
        try:
            answer = self._check(question_id, image_path, config)
        except AiRequestError as error:
            self._finish_failed(question_id, str(error))
        except Exception:  # noqa: BLE001 - a worker must never die silently
            logger.exception("ai job crashed: question=%d", question_id)
            self._finish_failed(question_id, "Внутренняя ошибка при проверке. Подробности в журнале сервера.")
        else:
            with self._lock:
                self._jobs.pop(question_id, None)
            logger.info(
                "ai job done: question=%d answer=%r input_tokens=%d output_tokens=%d",
                question_id,
                answer.answer,
                answer.input_tokens,
                answer.output_tokens,
            )

    def _check(self, question_id: int, image_path: Path, config: AzureConfig) -> AiAnswer:
        try:
            png = image_path.read_bytes()
        except OSError as error:
            logger.error("ai job: cannot read %s: %s", image_path, error)
            raise AiRequestError("Не удалось прочитать картинку вопроса.") from error

        reply = self._ask(config, question_id, png)

        summary = _sanitize.clean_text(reply.summary, SUMMARY_LIMIT)
        answer_text = _sanitize.clean_text(reply.answer, ANSWER_LIMIT)
        html = _sanitize.clean_html(reply.html).strip()
        if not html:
            if not answer_text:
                raise AiRequestError("Модель не вернула ни разбора, ни ответа. Попробуйте ещё раз.")
            html = f'<div class="verdict">{escape(answer_text)}</div>'

        stored = AiAnswer(
            question_id=question_id,
            html=html,
            summary=summary,
            answer=answer_text,
            model=reply.model,
            effort=_prompt.EFFORT,
            prompt_version=_prompt.PROMPT_VERSION,
            response_id=reply.response_id,
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            created_at=repo.now(),
        )
        conn = shared.get_connection(self._db_path())
        try:
            repo.save(conn, stored)
        finally:
            conn.close()
        return stored

    def _finish_failed(self, question_id: int, message: str) -> None:
        with self._lock:
            self._jobs[question_id] = _Job(state=STATE_ERROR, started_at=self._clock(), message=message)
        logger.warning("ai job failed: question=%d message=%s", question_id, message)
