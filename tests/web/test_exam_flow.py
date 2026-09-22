"""
End-to-end HTTP walk through starting, playing and resuming a session.

The fixture seeds exactly 3 available questions (ids 1-3), so "start a session
of every available question" is a fixed, predictable size across these tests.
"""

from __future__ import annotations


def test_menu_shows_the_catalog_state(logged_in_client):
    response = logged_in_client.get("/")
    assert response.status_code == 200
    assert "Доступно вопросов: 3 из 3" in response.text


def test_new_session_offers_the_whole_pool_when_below_every_preset(logged_in_client):
    response = logged_in_client.get("/session/new")
    assert response.status_code == 200
    assert "все 3" in response.text


def test_full_session_can_be_played_to_the_end(logged_in_client):
    start = logged_in_client.post("/session/new", data={"count": "3"})
    assert start.status_code == 200
    assert "Вопрос 1 из 3" in start.text

    answer = logged_in_client.get("/exam/answer")
    assert answer.status_code == 200
    assert 'id="grade-correct"' in answer.text

    second = logged_in_client.post("/exam/grade", data={"grade": "correct"})
    assert "Вопрос 2 из 3" in second.text

    third = logged_in_client.post("/exam/grade", data={"grade": "partial"})
    assert "Вопрос 3 из 3" in third.text

    finished = logged_in_client.post("/exam/grade", data={"grade": "incorrect"})
    assert "/results/" in str(finished.url)
    assert "Зачтено 3 из 3" in finished.text
    assert "правильно 1" in finished.text


def test_resuming_after_a_fresh_request_continues_where_it_left_off(logged_in_client):
    """
    Simulates a phone dropping the page after one answer and reopening it: no
    in-memory runner survives between these two calls, only the database does.
    """
    logged_in_client.post("/session/new", data={"count": "3"})
    logged_in_client.post("/exam/grade", data={"grade": "correct"})

    reloaded = logged_in_client.get("/exam")
    assert "Вопрос 2 из 3" in reloaded.text


def test_flagging_the_last_question_shortens_the_session(logged_in_client):
    """With every available question already drawn, a flag has nothing to replace it with."""
    logged_in_client.post("/session/new", data={"count": "3"})

    flagged = logged_in_client.post("/exam/flag")
    assert "Вопрос 1 из 2" in flagged.text


def test_abandon_ends_the_session_and_keeps_grades_already_given(logged_in_client):
    logged_in_client.post("/session/new", data={"count": "3"})
    logged_in_client.post("/exam/grade", data={"grade": "correct"})

    abandoned = logged_in_client.post("/exam/abandon")
    assert "/results/" in str(abandoned.url)
    assert "Зачтено 1 из 3" in abandoned.text


def test_menu_offers_to_resume_an_unfinished_session(logged_in_client):
    logged_in_client.post("/session/new", data={"count": "3"})

    menu = logged_in_client.get("/")
    assert "Продолжить" in menu.text


def test_auto_reveal_setting_skips_straight_to_the_answer(logged_in_client):
    logged_in_client.post(
        "/settings", data={"default_count": "3", "auto_reveal": "on"}
    )
    started = logged_in_client.post("/session/new", data={"count": "3"})

    assert "/exam" in str(started.url)
    assert 'id="grade-correct"' in started.text
