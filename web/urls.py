"""Turning a catalog image path into the URL that serves it."""

from __future__ import annotations

from pathlib import PurePosixPath


def image_url(relative_path: str) -> str:
    """
    Build the URL ``web.routes.images`` serves one prepared image at.

    Args:
        relative_path: Path as stored in the catalog, e.g.
            ``"images/q0001_question.png"`` (relative to ``DATA_DIR``).
    """
    return f"/img/{PurePosixPath(relative_path).name}"
