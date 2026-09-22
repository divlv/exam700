"""
Logging setup shared by every module of the AZ-700 exam simulator.

Configures the root logger to write to both the console and a rotating log file,
and stamps every record with a per-run identifier so that log lines produced by
different runs (dataset build, test execution, GUI session) can be told apart in
a single log file.

The run identifier is a random 7-character string from ``[a-z0-9]`` as required by
the project conventions, e.g. ``39z64rf``.

Example:
    >>> from api.modules.shared.api import get_logger, get_run_id
    >>> log = get_logger(__name__)
    >>> log.info("started")          # -> ... INFO [RunId: 39z64rf] __main__ - started
    >>> get_run_id()
    '39z64rf'
"""

from __future__ import annotations

import logging
import logging.handlers
import random
import string
import threading
from pathlib import Path

#: Characters allowed in a run identifier (project convention: lowercase alphanumeric).
_RUN_ID_ALPHABET = string.ascii_lowercase + string.digits

#: Length of a run identifier (project convention).
_RUN_ID_LENGTH = 7

#: Log file size before rotation, and how many old files to keep.
_MAX_LOG_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5

_LOG_FORMAT = "%(asctime)s %(levelname)-7s [RunId: %(run_id)s] %(name)s - %(message)s"

# Configuration must happen exactly once per process even though several modules
# call get_logger() independently; a lock keeps concurrent GUI/worker threads safe.
_lock = threading.Lock()
_configured = False
_run_id = "".join(random.choices(_RUN_ID_ALPHABET, k=_RUN_ID_LENGTH))


class _RunIdFilter(logging.Filter):
    """Injects the current run identifier into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id
        return True


def get_run_id() -> str:
    """
    Return the identifier of the current process run.

    Returns:
        The 7-character run identifier, stable for the lifetime of the process.
    """
    return _run_id


def configure_logging(log_file: Path, console_level: int = logging.INFO) -> None:
    """
    Attach console and rotating-file handlers to the root logger.

    Safe to call repeatedly: only the first call has an effect, so modules may
    call it defensively without duplicating handlers (which would double every
    log line).

    Args:
        log_file: Destination log file. Parent directories are created if missing.
        console_level: Level for console output. The file always records DEBUG so
            that diagnostics survive even when the console is kept quiet.
    """
    global _configured

    with _lock:
        if _configured:
            return

        log_file.parent.mkdir(parents=True, exist_ok=True)

        root = logging.getLogger()
        root.setLevel(logging.DEBUG)

        run_id_filter = _RunIdFilter()
        formatter = logging.Formatter(_LOG_FORMAT)

        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=_MAX_LOG_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(run_id_filter)
        root.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(console_level)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(run_id_filter)
        root.addHandler(console_handler)

        # Pillow logs one DEBUG record per chunk of every PNG it reads, which
        # buries this application's own lines. Its warnings still come through.
        logging.getLogger("PIL").setLevel(logging.WARNING)

        _configured = True


def get_logger(name: str) -> logging.Logger:
    """
    Return a logger for the given module name.

    Args:
        name: Logger name, normally the caller's ``__name__``.

    Returns:
        A standard :class:`logging.Logger`. Handlers come from the root logger,
        so :func:`configure_logging` must have run (the public ``shared.api``
        wrapper guarantees this).
    """
    return logging.getLogger(name)


# code-documentation-2026-09-05T18:24:39
