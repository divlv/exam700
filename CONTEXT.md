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

**Pass threshold**:
70% correct among counted answers. Only `correct` counts toward the
percentage; `partial` inflates the denominator but earns no credit. An empty
tally (nothing counted) never passes. Russian UI: *порог сдачи*.
