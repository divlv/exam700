# tests/architecture/_settings.py
# All comments are in English.

from __future__ import annotations

import logging
from pathlib import Path

APP = "<application_name>"  # Must be replaced by bootstrap script
MODULES_DIR = Path("src") / APP / "modules"

if APP in ("<application_name>", "", None):
    raise RuntimeError(
        "APP is not configured. Set APP in tests/architecture/_settings.py "
        "or run the bootstrap script that generates these tests."
    )

if not MODULES_DIR.exists():
    raise RuntimeError(
        f"MODULES_DIR does not exist: {MODULES_DIR}. "
        f"Check that APP='{APP}' is correct and that you use 'src' layout."
    )

MODULE_DIRS = [p for p in MODULES_DIR.iterdir() if p.is_dir()]
if not MODULE_DIRS:
    raise RuntimeError(
        f"No modules found under {MODULES_DIR}. Create at least one module folder."
    )

logger = logging.getLogger("architecture")
moduleNames = sorted([p.name for p in MODULE_DIRS])
logger.info("Architecture tests cover modules: %s", ", ".join(moduleNames))
