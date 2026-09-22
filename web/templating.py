"""
The single Jinja2Templates instance every route module renders through.

Resolved relative to this file rather than the process's working directory,
so the app renders correctly regardless of where ``uvicorn`` is launched from
(the working directory only needs to be right for ``main.py``'s own
``StaticFiles(directory="web/static")`` mount and the ``FileResponse`` for
``manifest.json``, both string literals used exactly once).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
