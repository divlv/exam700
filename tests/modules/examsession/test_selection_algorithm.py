"""
The selection-algorithm setting: adaptive (by history) or truly random.

The adaptive rule itself is covered in ``test_selection.py``. Here: the setting,
what "random" really means, and what a change of algorithm does to a session
that is already running.
"""

from __future__ import annotations

import random

import pytest

from api.modules.examsession import api as examsession
from api.modules.questionbank import api as questionbank
from api.modules.shared.api import get_connection
from tests.modules.examsession.test_selection import add_questions, record_history

C, P, I = "correct", "partial", "incorrect"

#: Enough seeds that a uniform draw over a handful of questions reaches every one
#: of them (the chance of missing one is below one in a million).
MANY_SEEDS = range(120)


def make_pool(tmp_path, name: str = "pool"):
    """
    A database of eight questions with a history that makes the adaptive plan predictable.

    7 and 8 were never answered; of the rest 3 is the worst (three incorrect),
    then 4 (two), then 5 (one); 1, 2 and 6 were answered correctly.
    """
    conn = get_connection(tmp_path / f"{name}.sqlite")
    questionbank.ensure_schema(conn)
    examsession.ensure_schema(conn)
    add_questions(conn, range(1, 9))
    record_history(conn, {1: [C], 2: [C], 6: [C], 3: [I, I, I], 4: [I, I], 5: [I]})
    return conn


@pytest.fixture()
def connection(tmp_path):
    conn = make_pool(tmp_path)
    yield conn
    conn.close()


def plan_ids(runner) -> list[int]:
    return [entry.question_id for entry in runner.entries()]


def stored_plan(conn, session_id: int) -> list[int]:
    """The plan as a fresh request would read it back from the database."""
    return plan_ids(examsession.resume_session(conn, session_id))


# --- the setting ---------------------------------------------------------


def test_the_adaptive_algorithm_is_the_default(connection):
    assert examsession.get_selection_algorithm(connection) == examsession.SELECTION_ADAPTIVE
    assert examsession.DEFAULT_SELECTION_ALGORITHM == examsession.SELECTION_ADAPTIVE


def test_the_choice_is_remembered_and_can_be_switched_back(connection):
    examsession.set_selection_algorithm(connection, examsession.SELECTION_RANDOM)
    assert examsession.get_selection_algorithm(connection) == "random"

    examsession.set_selection_algorithm(connection, examsession.SELECTION_ADAPTIVE)
    assert examsession.get_selection_algorithm(connection) == "adaptive"


def test_an_unknown_algorithm_is_refused_and_changes_nothing(connection):
    examsession.set_selection_algorithm(connection, examsession.SELECTION_RANDOM)

    with pytest.raises(ValueError):
        examsession.set_selection_algorithm(connection, "by-the-moon")

    assert examsession.get_selection_algorithm(connection) == "random"


def test_a_garbled_value_in_the_database_falls_back_to_the_default(connection):
    connection.execute(
        "INSERT INTO app_settings (key, value) VALUES ('selection_algorithm', 'garbage')"
    )
    connection.commit()

    assert examsession.get_selection_algorithm(connection) == examsession.SELECTION_ADAPTIVE


# --- random really is random ---------------------------------------------


def first_pick(conn, seed: int) -> int:
    runner = examsession.start_session(conn, 1, random.Random(seed))
    chosen = runner.current().id
    runner.abandon()
    return chosen


def test_random_ignores_the_history_that_adaptive_follows(connection):
    """Adaptive always opens with a never-answered question (7 or 8); random does not."""
    assert {first_pick(connection, seed) for seed in MANY_SEEDS} <= {7, 8}

    examsession.set_selection_algorithm(connection, examsession.SELECTION_RANDOM)
    picks = {first_pick(connection, seed) for seed in MANY_SEEDS}

    assert picks == set(range(1, 9))


def test_random_gives_every_available_question_about_the_same_chance(connection):
    examsession.set_selection_algorithm(connection, examsession.SELECTION_RANDOM)
    counts = {number: 0 for number in range(1, 9)}
    rng = random.Random(2026)
    for _ in range(2000):
        runner = examsession.start_session(connection, 1, rng)
        counts[runner.current().id] += 1
        runner.abandon()

    # 2000 draws over 8 questions: 250 each, give or take about 15. A
    # history-steered choice would put nearly all of them on 7 and 8.
    assert all(190 < count < 310 for count in counts.values()), counts


def test_random_never_draws_a_flagged_question_or_one_without_an_answer(connection):
    questionbank.mark_broken(connection, 3)
    questionbank.upsert_question(
        connection,
        questionbank.Question(
            id=5,
            source_file="x.pdf",
            start_page=0,
            end_page=1,
            question_image="images/q0005_question.png",
            answer_image="images/q0005_answer.png",
            has_answer=False,
        ),
    )
    connection.commit()
    examsession.set_selection_algorithm(connection, examsession.SELECTION_RANDOM)

    picks = {first_pick(connection, seed) for seed in MANY_SEEDS}

    assert picks == {1, 2, 4, 6, 7, 8}


def test_a_random_session_asks_distinct_questions(connection):
    examsession.set_selection_algorithm(connection, examsession.SELECTION_RANDOM)

    runner = examsession.start_session(connection, 8, random.Random(1))

    ids = plan_ids(runner)
    assert len(ids) == 8 and set(ids) == set(range(1, 9))


def test_a_quick_question_is_drawn_from_the_whole_base_when_random(connection):
    examsession.set_selection_algorithm(connection, examsession.SELECTION_RANDOM)
    picks = set()
    for seed in MANY_SEEDS:
        runner = examsession.start_quick_question(connection, random.Random(seed))
        picks.add(runner.current().id)
        runner.abandon()

    assert picks == set(range(1, 9))


def test_the_replacement_for_a_flagged_question_follows_the_current_algorithm(tmp_path):
    """Adaptive replaces it with the worst remaining question; random with any."""
    replacements = {}
    for algorithm in (examsession.SELECTION_ADAPTIVE, examsession.SELECTION_RANDOM):
        seen = set()
        for seed in range(40):
            conn = make_pool(tmp_path, f"{algorithm}-{seed}")
            examsession.set_selection_algorithm(conn, algorithm)
            runner = examsession.start_session(conn, 1, random.Random(seed))
            drawn = runner.current().id
            replacement = runner.flag_broken()
            assert replacement is not None and replacement.id != drawn
            seen.add((drawn, replacement.id))
            conn.close()
        replacements[algorithm] = seen

    # Adaptive: first an unanswered one, then (after it is flagged) the other
    # unanswered one, and only after both the worst answered one: never a good one.
    assert {replacement for _, replacement in replacements["adaptive"]} <= {7, 8}
    assert {replacement for _, replacement in replacements["random"]} >= {1, 2, 3, 4, 5, 6}


# --- switching while a session is running --------------------------------


def running_session(conn, seed: int = 1):
    """
    Five questions drawn adaptively, then: position 1 graded, 2 on screen,
    3 postponed, 4 and 5 still to come.
    """
    runner = examsession.start_session(conn, 5, random.Random(seed))
    runner.grade(examsession.GRADE_CORRECT)
    runner.mark(3)
    return runner


@pytest.mark.parametrize("seed", range(8))
def test_switching_redraws_only_the_questions_still_to_come(tmp_path, seed):
    conn = make_pool(tmp_path, f"switch-{seed}")
    runner = running_session(conn, seed)
    before = plan_ids(runner)

    changed = examsession.set_selection_algorithm(
        conn, examsession.SELECTION_RANDOM, random.Random(seed)
    )

    after = stored_plan(conn, runner.session.id)
    assert after[:3] == before[:3]  # graded, on screen, postponed: untouched
    assert len(after) == 5 and len(set(after)) == 5  # same length, no repeats
    assert changed == sum(1 for old, new in zip(before, after) if old != new)
    assert changed <= 2
    conn.close()


def test_the_redraw_follows_the_new_algorithm_not_the_old_one(tmp_path):
    """Adaptive would put 4 and 5 there; a random redraw sometimes puts others."""
    placed = set()
    for seed in range(30):
        conn = make_pool(tmp_path, f"follow-{seed}")
        runner = running_session(conn, seed)
        examsession.set_selection_algorithm(conn, examsession.SELECTION_RANDOM, random.Random(seed))
        placed |= set(stored_plan(conn, runner.session.id)[3:])
        conn.close()

    assert placed - {4, 5}, "random never replaced the adaptive choice"


def test_switching_back_to_adaptive_restores_the_history_driven_choice(tmp_path):
    conn = make_pool(tmp_path)
    examsession.set_selection_algorithm(conn, examsession.SELECTION_RANDOM)
    runner = examsession.start_session(conn, 5, random.Random(3))
    runner.grade(examsession.GRADE_CORRECT)  # position 1 graded, 2..5 to come

    examsession.set_selection_algorithm(conn, examsession.SELECTION_ADAPTIVE, random.Random(3))

    after = stored_plan(conn, runner.session.id)
    # Position 1 stays; of what is left (2..5) adaptive picks never-answered
    # questions first, then the worst, among those not already in the plan.
    assert len(set(after)) == 5
    upcoming = after[2:]  # position 2 is on screen and stays, 3..5 are redrawn
    unanswered_left = {7, 8} - set(after[:2])
    assert set(upcoming[: len(unanswered_left)]) == unanswered_left
    conn.close()


def test_saving_the_same_algorithm_leaves_the_plan_alone(tmp_path):
    conn = make_pool(tmp_path)
    runner = running_session(conn)
    before = stored_plan(conn, runner.session.id)

    assert examsession.set_selection_algorithm(conn, examsession.SELECTION_ADAPTIVE) == 0
    assert stored_plan(conn, runner.session.id) == before

    examsession.set_selection_algorithm(conn, examsession.SELECTION_RANDOM, random.Random(1))
    settled = stored_plan(conn, runner.session.id)
    assert examsession.set_selection_algorithm(conn, examsession.SELECTION_RANDOM) == 0
    assert stored_plan(conn, runner.session.id) == settled
    conn.close()


def test_switching_with_no_session_in_progress_just_stores_the_choice(connection):
    assert examsession.set_selection_algorithm(connection, examsession.SELECTION_RANDOM) == 0
    assert examsession.get_selection_algorithm(connection) == "random"


def test_switching_on_the_last_question_has_nothing_to_redraw(connection):
    runner = examsession.start_session(connection, 2, random.Random(1))
    runner.grade(examsession.GRADE_CORRECT)  # the second question is now on screen
    before = stored_plan(connection, runner.session.id)

    assert examsession.set_selection_algorithm(connection, examsession.SELECTION_RANDOM) == 0
    assert stored_plan(connection, runner.session.id) == before


def test_switching_during_a_quick_question_leaves_it_and_applies_to_the_next(connection):
    runner = examsession.start_quick_question(connection, random.Random(1))
    shown = runner.current().id

    examsession.set_selection_algorithm(connection, examsession.SELECTION_RANDOM)

    assert examsession.resume_session(connection, runner.session.id).current().id == shown
    runner.abandon()
    examsession.start_quick_question(connection, random.Random(2)).abandon()  # drawn at random now


def test_a_redraw_never_brings_back_a_flagged_question(tmp_path):
    conn = make_pool(tmp_path)
    questionbank.mark_broken(conn, 6)
    conn.commit()
    runner = running_session(conn)

    for seed in range(20):
        examsession.set_selection_algorithm(conn, examsession.SELECTION_RANDOM, random.Random(seed))
        assert 6 not in stored_plan(conn, runner.session.id)
        examsession.set_selection_algorithm(conn, examsession.SELECTION_ADAPTIVE, random.Random(seed))
        assert 6 not in stored_plan(conn, runner.session.id)
    conn.close()


def test_the_session_still_plays_to_the_end_after_a_switch(tmp_path):
    conn = make_pool(tmp_path)
    runner = running_session(conn)
    examsession.set_selection_algorithm(conn, examsession.SELECTION_RANDOM, random.Random(4))

    runner = examsession.resume_session(conn, runner.session.id)
    shown = []
    while not runner.finished:
        shown.append(runner.current().id)
        runner.grade(examsession.GRADE_CORRECT)
    runner.finish()

    assert len(shown) == len(set(shown))  # nothing shown twice
    conn.close()
