# AZ-700 Exam Trainer

A personal, single-user trainer for the Microsoft AZ-700 exam. A **question**
is not text: the source PDFs have no text layer, so every question and its
answer are pre-rendered images. This glossary defines the vocabulary shared by
`api/modules/*` (reused unchanged from the original desktop build) and the
`web/` layer built on top of it.

## Language

**Question**:
One exam question, identified by the number printed in the source PDF
(1..369), stored as a pair of images (question, answer) plus the geometry
they were cut from. Russian UI: *вопрос*.

**Available question**:
A question that may be drawn for a session: the source PDF printed an answer
for it, and the user has not flagged it. `questionbank.count_available` /
`is_available`. Russian UI: *доступный вопрос*.
_Avoid_: "active question", "usable question".

**Unanswered question**:
A question whose source PDF printed the `Correct Answer:` label with no
letter after it - a defect in the source material, not an extraction bug.
Permanently excluded from every session; shown for information only on the
administration screen. Russian UI: *вопрос без ответа в исходнике*.

**Badly-cropped flag**:
The user's own mark that a question's image was cut incorrectly. Global and
retroactive: a flagged question drops out of every past and future session's
statistics until the flag is removed. `questionbank.mark_broken` /
`unmark_broken`. Russian UI: *отметка «вопрос некорректный»*.
_Avoid_: "broken question" alone, without the flag/administration context.

**Session**:
One run through a fixed number of questions, from start to finish or
abandonment. Russian UI: *сессия*.

**Session plan**:
The ordered list of questions a session draws, decided once when the session
starts and durable in the database (`session_questions`) rather than kept in
process memory. A flag-and-replace during the session mutates this plan in
place; grading never does. This is what lets a session survive a page reload,
a phone dropping its tab, or a pod restart - see
[docs/adr/0001-persist-session-plan.md](docs/adr/0001-persist-session-plan.md).
_Avoid_: "runner state" (that is the in-memory `SessionRunner` rebuilt fresh
from the plan on every request - an implementation detail, not a domain term).

**Mark** (postpone):
The user's "come back to this" flag on a question of a running session. A
marked question has no grade, so it is not counted for now (but becomes a
*skipped question*, counted as incorrect, if never returned to); the normal pass
skips it and the review screen offers it again. Grading it clears the mark. Only
a question without a grade can be marked. Stored on the session plan, so it
survives a reload. Not to be confused with the *badly-cropped flag*, which
removes a question from the pool for good. Russian UI: *отметить на потом* (🚩).
_Avoid_: "flag" for this (that word is taken by the badly-cropped flag).

**Review screen**:
The list of every question of a session with its state - not yet shown,
marked, or graded with its grade - from which any question can be reopened and
graded or re-graded (a new grade replaces the old one). Opens by itself when the
normal pass ends with marked questions left; if everything is graded the test
simply finishes. Finishing from it with ungraded questions is allowed: they
count as incorrect (see *Skipped question*). Russian UI: *обзор*.

**Skipped question**:
A question of a session that still has no grade when the test is finished -
postponed with the mark, or never shown. As in an exam it counts as incorrect:
it is stored as an ordinary incorrect answer worth 0 points and enters the
answer history like one. Abandoning the session is different: the user is
leaving, and only the grades actually given are kept. Russian UI: *пропущенный
вопрос*.

**Question history**:
How many times the user has graded one question correct, partial and incorrect,
across every session including abandoned ones. It is read straight from the
stored answers, so resetting the statistics empties it. A question with no
answers at all is *unanswered* in this sense (not to be confused with the
*unanswered question* above, which the source never answered).
Russian UI: *журнал ответов*.

**Selection window**:
The ten random available questions that compete each time the next question is
chosen. A never-answered question in the window wins (random among those);
otherwise the one with the most incorrect answers wins; every tie is broken at
random. Correct and partial answers are not told apart. Fewer than ten compete
when fewer are left.

**Grade**:
The user's own self-assessment of one answer: `correct`, `partial`, or
`incorrect`. There is no automatic grading - the user reads the answer image
and judges themselves. Russian UI: *оценка* (Правильно / Частично /
Неправильно).
_Avoid_: "score" for a single grade (score/tally is the aggregate, see Tally).

**Counted answer**:
A stored grade whose question is still available right now. Only counted
answers contribute to a tally; a grade is never deleted, so removing a flag
brings it back into the count. Russian UI: *зачтённый ответ*.
_Avoid_: "valid answer", "active answer".

**Tally**:
Counts of correct/partial/incorrect among a session's (or the whole history's)
counted answers, with the derived pass/fail verdict.

**Credit**:
The points one answer earned at the moment it was graded, stored with the
answer: `correct` = 1, `partial` = 0.34, `incorrect` = 0. Stored, not derived
from the grade, so changing the rule only affects answers graded afterwards.
Answers graded before partial credit existed keep 0 for `partial`.
_Avoid_: recomputing a score from the grade.

**Score**:
The sum of credit over a tally's counted answers, shown as points out of the
number of counted answers and as a percentage of that maximum. Eight correct,
one partial and one incorrect of ten is 8.34 points, 83.4%. One question in a
set of N is worth 100/N percentage points. Russian UI: *балл*, *оценка*.
_Avoid_: using "percent correct" for the score - that is the separate share of
answers graded correct.

**Pass threshold**:
A score of at least 70%. An empty tally (nothing counted) never passes.
Russian UI: *порог сдачи*.
