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

from api.modules.aiassist import api as aiassist
from api.modules.examsession import api as examsession
from web.version import get_git_commit

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Scoring constants shown in the screens' explanatory text, so the wording can
# never drift from the values the backend actually applies.
templates.env.globals["PASS_THRESHOLD_PERCENT"] = examsession.PASS_THRESHOLD_PERCENT
templates.env.globals["PARTIAL_CREDIT"] = examsession.PARTIAL_CREDIT_PERCENT / 100

# How each question-selection algorithm is presented, kept in one place so the
# settings screen and the badge on the question screen always say the same thing.
templates.env.globals["SELECTION_ALGORITHMS"] = examsession.SELECTION_ALGORITHMS
templates.env.globals["SELECTION_LABELS"] = {
    examsession.SELECTION_ADAPTIVE: {
        "icon": "🎯",
        "title": "Адаптивный",
        "hint": "Сначала вопросы, на которые вы ещё не отвечали, потом те, где больше всего ошибок.",
    },
    examsession.SELECTION_RANDOM: {
        "icon": "🎲",
        "title": "Случайный",
        "hint": "Любой вопрос из всей базы с равной вероятностью, история ответов не учитывается.",
    },
}

# Shown in every page's footer to tell deployed versions apart.
templates.env.globals["GIT_COMMIT"] = get_git_commit()

# Whether the "AI" buttons are live: false while the Azure OpenAI settings are missing.
templates.env.globals["ai_enabled"] = aiassist.is_configured
