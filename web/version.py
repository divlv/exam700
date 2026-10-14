"""
Which build of the application is running, so versions can be told apart.

The short commit hash is shown in the footer of every page. The container image
has no ``.git`` directory, so CI passes the commit in at build time and the
Dockerfile exposes it as ``AZ700_GIT_COMMIT``; a local run from a checkout asks
git instead.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

#: Environment variable the Docker image sets from the CI ``GIT_COMMIT`` build arg.
ENV_VAR = "AZ700_GIT_COMMIT"

#: Length of the hash shown to the user, matching the ``sha-xxxxxxx`` image tag.
SHORT_LENGTH = 7

#: What is shown when the commit cannot be determined.
UNKNOWN = "unknown"

#: Repository root, used only for the local-run fallback below.
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _from_git() -> str | None:
    """Ask git for the checked-out commit, or ``None`` outside a git checkout."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", f"--short={SHORT_LENGTH}", "HEAD"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and commit else None


def get_git_commit() -> str:
    """
    Return the short hash of the commit this process is running.

    Order of preference: the build-time value in ``AZ700_GIT_COMMIT`` (the
    deployed container), then ``git`` (a local run from a checkout), then
    ``"unknown"``.
    """
    from_env = os.environ.get(ENV_VAR, "").strip()
    if from_env and from_env != UNKNOWN:
        return from_env[:SHORT_LENGTH]
    return (_from_git() or UNKNOWN)[:SHORT_LENGTH]
