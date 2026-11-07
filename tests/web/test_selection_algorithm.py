"""
The selection-algorithm setting through HTTP: the settings screen and the badge
on the question screen.

How the algorithms choose, and what a switch does to the plan of a running
session, is covered in ``tests/modules/examsession/test_selection_algorithm.py``.
"""

from __future__ import annotations

import re


def _radio(page: str, value: str) -> str:
    """The tag of the radio button with this value."""
    match = re.search(rf'<input type="radio" name="selection_algorithm" value="{value}"[^>]*>', page)
    assert match, f"no radio for {value}"
    return match.group(0)


def _save(client, **fields):
    data = {"default_count": "3", **fields}
    return client.post("/settings", data=data)


def test_the_settings_offer_both_algorithms_with_adaptive_chosen_by_default(logged_in_client):
    page = logged_in_client.get("/settings").text

    assert "Как выбираются вопросы" in page
    assert "Адаптивный" in page and "Случайный" in page
    assert "checked" in _radio(page, "adaptive")
    assert "checked" not in _radio(page, "random")


def test_choosing_random_is_saved_and_survives_a_reload(logged_in_client):
    saved = _save(logged_in_client, selection_algorithm="random")

    assert "Сохранено" in saved.text
    assert "checked" in _radio(saved.text, "random")
    reopened = logged_in_client.get("/settings").text
    assert "checked" in _radio(reopened, "random")
    assert "checked" not in _radio(reopened, "adaptive")


def test_an_unknown_value_leaves_the_stored_choice_alone(logged_in_client):
    _save(logged_in_client, selection_algorithm="random")

    response = _save(logged_in_client, selection_algorithm="by-the-moon")

    assert response.status_code == 200
    assert "checked" in _radio(response.text, "random")


def test_a_form_without_the_field_does_not_reset_the_choice(logged_in_client):
    """The settings form used to be sent without it, and other callers may still do so."""
    _save(logged_in_client, selection_algorithm="random")

    response = _save(logged_in_client)  # no selection_algorithm at all

    assert "checked" in _radio(response.text, "random")


def test_the_settings_say_how_many_upcoming_questions_were_drawn_again(logged_in_client):
    with_changes = logged_in_client.get("/settings?saved=1&redrawn=2").text
    without = logged_in_client.get("/settings?saved=1&redrawn=0").text

    assert "выбраны заново: 2" in with_changes
    assert "выбраны заново" not in without


# --- the badge on the question screen -------------------------------------


def _badge(page: str) -> str:
    match = re.search(r'<span class="badge badge-muted" id="selection-badge"[^>]*>(.*?)</span>', page, re.S)
    assert match, "no selection badge on the page"
    return match.group(1).strip()


def test_the_question_screen_shows_the_algorithm_in_force(logged_in_client):
    logged_in_client.post("/session/new", data={"count": "3"})

    assert "Адаптивный" in _badge(logged_in_client.get("/exam").text)


def test_the_badge_follows_a_switch_made_in_the_middle_of_a_session(logged_in_client):
    logged_in_client.post("/session/new", data={"count": "3"})
    assert "Адаптивный" in _badge(logged_in_client.get("/exam").text)

    _save(logged_in_client, selection_algorithm="random")
    assert "Случайный" in _badge(logged_in_client.get("/exam").text)

    _save(logged_in_client, selection_algorithm="adaptive")
    assert "Адаптивный" in _badge(logged_in_client.get("/exam").text)


def test_the_badge_is_on_the_answer_view_and_on_a_quick_question_too(logged_in_client):
    _save(logged_in_client, selection_algorithm="random")
    logged_in_client.post("/session/quick")

    assert "Случайный" in _badge(logged_in_client.get("/exam").text)
    assert "Случайный" in _badge(logged_in_client.get("/exam/answer").text)


def test_the_badge_explains_itself_on_hover(logged_in_client):
    logged_in_client.post("/session/new", data={"count": "3"})

    page = logged_in_client.get("/exam").text

    assert "Меняется в настройках" in page and "со следующего вопроса" in page


def test_a_session_started_after_choosing_random_still_plays_normally(logged_in_client):
    _save(logged_in_client, selection_algorithm="random")
    logged_in_client.post("/session/new", data={"count": "3"})

    for _ in range(3):
        assert logged_in_client.get("/exam").status_code == 200
        logged_in_client.post("/exam/grade", data={"grade": "correct"})

    results = logged_in_client.get("/results")
    assert results.status_code == 200
