"""Settings, and the administration screen's resets and badly-cropped flags."""

from __future__ import annotations


def _finish_a_session(client) -> int:
    """Play the 3-question fixture pool to completion and return the session id."""
    client.post("/session/new", data={"count": "3"})
    client.post("/exam/grade", data={"grade": "correct"})
    client.post("/exam/grade", data={"grade": "correct"})
    finished = client.post("/exam/grade", data={"grade": "correct"})
    return int(finished.url.path.rsplit("/", 1)[-1])


def test_settings_round_trip(logged_in_client):
    saved = logged_in_client.post(
        "/settings", data={"default_count": "2", "auto_reveal": "on"}
    )
    assert "Сохранено" in saved.text
    assert 'value="2"' in saved.text
    assert "checked" in saved.text

    # A plain reload (no "saved" query flag) must still show the stored value.
    reopened = logged_in_client.get("/settings")
    assert 'value="2"' in reopened.text
    assert "checked" in reopened.text
    assert "Сохранено" not in reopened.text


def test_flagging_from_session_review_hides_the_question_from_new_sessions(
    logged_in_client,
):
    session_id = _finish_a_session(logged_in_client)

    review = logged_in_client.get(f"/results/{session_id}")
    assert review.status_code == 200

    flagged = logged_in_client.post(
        f"/results/{session_id}/flag", data={"question_id": "1"}
    )
    assert flagged.status_code == 200

    menu = logged_in_client.get("/")
    assert "Доступно вопросов: 2 из 3" in menu.text
    assert "Помечено некорректными: 1" in menu.text


def test_unmark_broken_restores_the_question(logged_in_client):
    session_id = _finish_a_session(logged_in_client)
    logged_in_client.post(f"/results/{session_id}/flag", data={"question_id": "1"})

    broken_tab = logged_in_client.get("/admin?tab=broken")
    assert "№1" in broken_tab.text

    logged_in_client.post("/admin/broken/1/unmark")

    menu = logged_in_client.get("/")
    assert "Доступно вопросов: 3 из 3" in menu.text


def test_reset_one_session_removes_it(logged_in_client):
    session_id = _finish_a_session(logged_in_client)

    stats_tab = logged_in_client.get("/admin?tab=stats")
    assert stats_tab.status_code == 200

    logged_in_client.post(f"/admin/sessions/{session_id}/reset")

    missing = logged_in_client.get(f"/results/{session_id}", follow_redirects=False)
    assert missing.headers["location"] == "/results?missing=1"


def test_reset_all_sessions_clears_the_results_list(logged_in_client):
    _finish_a_session(logged_in_client)

    logged_in_client.post("/admin/sessions/reset-all")

    results_page = logged_in_client.get("/results")
    assert "Сессий ещё не было." in results_page.text
