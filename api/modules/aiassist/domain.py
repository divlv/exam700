"""
Domain model of the AI check: what the model said about one question.

A question is only a picture, so the "check" is the model's own reading of that
picture: its answer, a one-line description of what the question was about, and
a formatted explanation. All of it is kept, because every request to the model
is paid for and a question is never asked twice.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Where a request for one question stands.
STATE_IDLE = "idle"  # never asked, nothing running
STATE_RUNNING = "running"  # the model is working on it right now
STATE_DONE = "done"  # the answer is stored
STATE_ERROR = "error"  # the last attempt failed; nothing was stored

#: Ways to order the list of checked questions.
SORT_DATE = "date"
SORT_QUESTION = "question"
DIRECTION_ASC = "asc"
DIRECTION_DESC = "desc"


class AiNotConfigured(RuntimeError):
    """Raised when the Azure OpenAI settings are missing or malformed."""


@dataclass(frozen=True)
class AiAnswer:
    """
    One stored answer of the model.

    Attributes:
        question_id: Question number that was checked.
        html: Sanitised HTML fragment with the explanation, ready to be shown
            inside the page wrapper.
        summary: One line saying what the question is about, written by the
            model (the question has no text of its own to list).
        answer: The model's short final answer, e.g. ``"B"`` or ``"A, D"``.
        model: Deployment that produced it.
        effort: Reasoning effort the request used.
        prompt_version: Version of the instructions sent with the picture.
        response_id: The service's identifier of the response.
        input_tokens: Tokens the request consumed.
        output_tokens: Tokens the model produced, reasoning included.
        created_at: ISO-8601 UTC timestamp of when it was stored.
    """

    question_id: int
    html: str
    summary: str
    answer: str
    model: str
    effort: str
    prompt_version: int
    response_id: str
    input_tokens: int
    output_tokens: int
    created_at: str


@dataclass(frozen=True)
class AiStatus:
    """
    Where a request for one question stands, for the page that polls it.

    Attributes:
        state: One of the ``STATE_*`` constants.
        elapsed_seconds: How long a running request has been going.
        message: Why the last attempt failed, safe to show to the user.
        answer: The stored answer once ``state`` is :data:`STATE_DONE`.
    """

    state: str
    elapsed_seconds: float = 0.0
    message: str = ""
    answer: AiAnswer | None = None
