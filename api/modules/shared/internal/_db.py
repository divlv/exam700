"""
SQLite access and schema migration helper shared by all modules.

Each module owns its own tables and declares its own DDL; this helper only opens
connections and tracks which migration version has been applied per component.
That keeps ``questionbank`` (the ``questions`` table) and, later, ``examsession``
(the session/statistics tables) independent of each other while still sharing one
database file.

Example:
    >>> conn = connect(Path("data/az700.sqlite"))
    >>> apply_migrations(conn, "questionbank", [(1, ["CREATE TABLE t(id INTEGER)"])])
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

#: DDL for the migration bookkeeping table itself.
#: Keyed by component so every module versions its schema independently.
_SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    component TEXT PRIMARY KEY,
    version   INTEGER NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """
    Open a SQLite connection with the settings this project relies on.

    Args:
        db_path: Path to the database file. Parent directories are created if missing.

    Returns:
        An open connection with :class:`sqlite3.Row` rows, foreign keys enforced
        and WAL journaling enabled.

    Note:
        Foreign keys are off by default in SQLite and must be enabled per
        connection, otherwise ``ON DELETE CASCADE`` (used when resetting a
        session's statistics) silently does nothing.

    Note:
        ``check_same_thread=False`` and a busy timeout are required for the web
        server: the desktop GUI opened one connection on the Tk thread for the
        whole process, but a web request may run on a different thread than the
        one that opened the connection, and two requests can briefly overlap
        while WAL checkpoints. Neither setting affects the desktop build, which
        never has more than one thread touching the connection at a time.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL keeps reads from blocking while the dataset build writes 370 rows.
    conn.execute("PRAGMA journal_mode = WAL")
    # Wait for a locked database instead of failing immediately, so a request
    # that overlaps a write does not surface "database is locked" to the user.
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def apply_migrations(
    conn: sqlite3.Connection,
    component: str,
    migrations: list[tuple[int, list[str]]],
) -> int:
    """
    Bring one component's schema up to the newest declared version.

    Migrations already recorded for the component are skipped, so this is safe to
    call on every start-up. All pending statements run inside a single
    transaction: a failure leaves the recorded version untouched rather than half
    of a migration applied.

    Args:
        conn: Open connection from :func:`connect`.
        component: Name of the owning module, e.g. ``"questionbank"``.
        migrations: Pairs of ``(version, statements)``, ordered by ascending
            version. Versions start at 1 and must not have gaps.

    Returns:
        The schema version in effect after the call.

    Raises:
        sqlite3.Error: If any statement fails; the transaction is rolled back.
    """
    conn.execute(_SCHEMA_VERSION_DDL)

    row = conn.execute(
        "SELECT version FROM schema_version WHERE component = ?", (component,)
    ).fetchone()
    current = row["version"] if row else 0

    pending = [(v, stmts) for v, stmts in sorted(migrations) if v > current]
    if not pending:
        return current

    try:
        for version, statements in pending:
            for statement in statements:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_version (component, version) VALUES (?, ?) "
                "ON CONFLICT(component) DO UPDATE SET version = excluded.version, "
                "applied_at = datetime('now')",
                (component, version),
            )
            current = version
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise

    return current


# code-documentation-2026-09-05T18:24:39
