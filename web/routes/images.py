"""Serves the pre-rendered question/answer PNGs from the data volume."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from api.modules.shared import api as shared
from web.deps import require_login

router = APIRouter()

#: The exact naming scheme the ``ingest`` build writes: ``qNNNN_question.png``
#: / ``qNNNN_answer.png``. Validating against it - rather than trusting the
#: path segment - means a crafted name can neither escape ``IMAGES_DIR`` nor
#: probe for unrelated files on the volume.
_IMAGE_NAME_RE = re.compile(r"^q\d{4}_(question|answer)\.png$")


@router.get("/img/{name}")
def serve_image(name: str, _: str = Depends(require_login)) -> FileResponse:
    """Serve one prepared image. Behind login, like the rest of the exam content."""
    if not _IMAGE_NAME_RE.fullmatch(name):
        raise HTTPException(status_code=404)

    path = shared.IMAGES_DIR / name
    if not path.is_file():
        raise HTTPException(status_code=404)

    # Every image is written once by the build and never rewritten under the
    # same name, so the browser can cache it forever - this matters on a
    # phone, where re-fetching a 200-700 KB PNG on every visit is real cost.
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
