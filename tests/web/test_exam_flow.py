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
    assert "page-exam" not in response.text, "only the exam screen gets the wide layout"


def test_new_session_offers_the_whole_pool_when_below_every_preset(logged_in_client):
    response = logged_in_client.get("/session/new")
    assert response.status_code == 200
    assert "все 3" in response.text


def test_full_session_can_be_played_to_the_end(logged_in_client):
    start = logged_in_client.post("/session/new", data={"count": "3"})
    assert start.status_code == 200
    assert "Вопрос 1 из 3" in start.text
    assert 'class="page page-exam"' in start.text
    assert 'data-action="copy-image"' in start.text

    answer = logged_in_client.get("/exam/answer")
    assert answer.status_code == 200
    assert 'id="grade-correct"' in answer.text
    assert 'data-action="copy-image"' in answer.text

    second = logged_in_client.post("/exam/grade", data={"grade": "correct"})
    assert "Вопрос 2 из 3" in second.text

    third = logged_in_client.post("/exam/grade", data={"grade": "partial"})
    assert "Вопрос 3 из 3" in third.text

    finished = logged_in_client.post("/exam/grade", data={"grade": "incorrect"})
    assert "/results/" in str(finished.url)
    assert "Зачтено 3 из 3" in finished.text
    assert "правильно 1" in finished.text
    # One correct (1.00) + one partial (0.34) + one incorrect (0) of three.
    assert "1.34 из 3" in finished.text
    assert "44.7%" in finished.text


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


# ---------------------------------------------------------------------------
# Postponing a question ("come back to this") and the review screen
# ---------------------------------------------------------------------------


def _start(client, count="3"):
    return client.post("/session/new", data={"count": count})


def test_the_exam_screen_offers_the_mark_and_the_review(logged_in_client):
    page = _start(logged_in_client)

    assert 'id="mark-btn"' in page.text
    assert "Отметить на потом" in page.text
    assert 'href="/exam/review"' in page.text


def test_postponing_a_question_moves_on_without_grading_it(logged_in_client):
    _start(logged_in_client)

    after = logged_in_client.post("/exam/mark", data={"position": "1"})

    assert "Вопрос 2 из 3" in after.text


def test_the_review_opens_when_the_unmarked_questions_run_out(logged_in_client):
    _start(logged_in_client)
    logged_in_client.post("/exam/mark", data={"position": "1"})
    logged_in_client.post("/exam/grade", data={"grade": "correct", "position": "2"})

    last = logged_in_client.post("/exam/grade", data={"grade": "partial", "position": "3"})

    assert last.url.path == "/exam/review"
    assert "Оценено 2 из 3" in last.text
    assert "🚩 отмечен" in last.text
    assert "К отмеченным" in last.text


def test_a_postponed_question_is_reopened_and_graded_from_the_review(logged_in_client):
    _start(logged_in_client)
    logged_in_client.post("/exam/mark", data={"position": "1"})
    logged_in_client.post("/exam/grade", data={"grade": "correct", "position": "2"})
    logged_in_client.post("/exam/grade", data={"grade": "correct", "position": "3"})

    reopened = logged_in_client.get("/exam/q/1")
    assert "Вопрос 1 из 3" in reopened.text
    assert "Снять отметку" in reopened.text

    finished = logged_in_client.post("/exam/grade", data={"grade": "incorrect", "position": "1"})

    assert "/results/" in str(finished.url)
    assert "Зачтено 3 из 3" in finished.text


def test_taking_the_mark_off_returns_to_the_same_question(logged_in_client):
    _start(logged_in_client)
    logged_in_client.post("/exam/mark", data={"position": "1"})

    back = logged_in_client.post("/exam/unmark", data={"position": "1"})

    assert back.url.path == "/exam/q/1"
    assert "Отметить на потом" in back.text


def test_an_already_graded_question_can_be_regraded_from_the_review(logged_in_client):
    _start(logged_in_client)
    logged_in_client.post("/exam/grade", data={"grade": "correct", "position": "1"})

    opened = logged_in_client.get("/exam/q/1/answer")
    assert "уже оценён: правильно" in opened.text
    assert "Отметить на потом" not in opened.text, "a graded question cannot be postponed"

    logged_in_client.post("/exam/grade", data={"grade": "partial", "position": "1"})

    review = logged_in_client.get("/exam/review")
    assert "частично" in review.text
    assert "Оценено 1 из 3" in review.text


def test_postponing_a_graded_question_is_refused(logged_in_client):
    _start(logged_in_client)
    logged_in_client.post("/exam/grade", data={"grade": "correct", "position": "1"})

    refused = logged_in_client.post("/exam/mark", data={"position": "1"})

    assert refused.url.path == "/exam/review"
    assert "🚩 отмечен" not in refused.text


def test_finishing_from_the_review_counts_unresolved_questions_as_incorrect(logged_in_client):
    _start(logged_in_client)
    logged_in_client.post("/exam/mark", data={"position": "1"})
    logged_in_client.post("/exam/grade", data={"grade": "correct", "position": "2"})
    logged_in_client.post("/exam/grade", data={"grade": "correct", "position": "3"})

    review = logged_in_client.get("/exam/review")
    assert "будут засчитаны как неправильные" in review.text
    assert (
        'data-confirm="Завершить тест? Вопросов без оценки: 1 — они будут засчитаны как неправильные.'
        in review.text
    )

    results = logged_in_client.post("/exam/finish")

    assert "/results/" in str(results.url)
    assert "Зачтено 3 из 3" in results.text
    assert "правильно 2 · частично 0 · неправильно 1" in results.text
    assert "66.7%" in results.text


def test_abandoning_does_not_count_the_questions_left_unanswered(logged_in_client):
    """Leaving the test is not finishing it: only the grades given are kept."""
    _start(logged_in_client)
    logged_in_client.post("/exam/mark", data={"position": "1"})
    logged_in_client.post("/exam/grade", data={"grade": "correct", "position": "2"})

    results = logged_in_client.post("/exam/abandon")

    assert "Зачтено 1 из 3" in results.text


def test_a_position_outside_the_session_goes_to_the_review(logged_in_client):
    _start(logged_in_client)

    page = logged_in_client.get("/exam/q/99")

    assert page.url.path == "/exam/review"


def test_the_marks_survive_leaving_and_coming_back(logged_in_client):
    _start(logged_in_client)
    logged_in_client.post("/exam/mark", data={"position": "2"})

    logged_in_client.get("/")  # leave the exam screen
    review = logged_in_client.get("/exam/review")

    assert "🚩 отмечен" in review.text
    assert "ещё не показано 2" in review.text
