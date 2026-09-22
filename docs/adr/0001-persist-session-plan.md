# Persist the session plan in the database instead of process memory

The desktop build kept a session's drawn question order in the `SessionRunner`
object, living for the lifetime of one long-running Tk process - fine there,
since nothing else could ever observe or restart that process mid-session. The
web app has no such guarantee: a request is stateless, and a phone browser can
discard its tab, lose network, or simply be reloaded at any point, with no way
to reach whatever process instance last held the runner.

We considered keeping the runner in an in-memory dict keyed by session id
(cheapest to build) and reconstructing it from `session_answers` alone
(no schema change, but ambiguous once a flag-and-replace has happened mid
-session). We instead added a `session_questions` table recording the drawn
plan itself (schema v2 of `examsession`), and rebuild a `SessionRunner` from
it plus the stored grades on every request via `examsession.resume_session`.
This is the only way a mobile session survives a dropped tab, a pod restart,
or the process simply not being the one that started it - at the cost of one
extra table and a few extra writes on every flag-and-replace.
