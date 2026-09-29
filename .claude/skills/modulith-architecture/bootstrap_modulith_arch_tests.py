# .codex/skills/modulith-architecture/bootstrap_modulith_arch_tests.py
# All comments are in English.

from __future__ import annotations

import shutil
from pathlib import Path


SKILL_DIR = Path(".codex/skills/modulith-architecture")
TEMPLATES_DIR = SKILL_DIR / "templates"
DEST_DIR = Path("tests/architecture")


def detect_app_package() -> str:
    """
    Detect <app> by finding src/<app>/modules directory.
    """
    src = Path("src")
    if not src.exists():
        raise RuntimeError("Cannot find 'src/' directory. This bootstrap expects src-layout projects.")

    candidates = []
    for p in src.iterdir():
        if not p.is_dir():
            continue
        if (p / "modules").is_dir():
            candidates.append(p.name)

    if not candidates:
        raise RuntimeError("Cannot detect app package: no 'src/<app>/modules/' directory found.")

    if len(candidates) > 1:
        raise RuntimeError(
            f"Multiple candidates found for app package: {candidates}. "
            "Please keep a single src/<app>/modules layout or set APP manually."
        )

    return candidates[0]


def copy_templates(app: str) -> None:
    if not TEMPLATES_DIR.exists():
        raise RuntimeError(f"Missing templates directory: {TEMPLATES_DIR}")

    DEST_DIR.mkdir(parents=True, exist_ok=True)
    (Path("tests") / "__init__.py").touch(exist_ok=True)
    DEST_DIR.joinpath("__init__.py").touch(exist_ok=True)

    template_files = [
        TEMPLATES_DIR / "tests/architecture/_settings.py",
        TEMPLATES_DIR / "tests/architecture/test_boundaries.py",
        TEMPLATES_DIR / "tests/architecture/test_cycles.py",
    ]

    for src_file in template_files:
        if not src_file.exists():
            raise RuntimeError(f"Missing template file: {src_file}")

        dst_file = DEST_DIR / src_file.name
        shutil.copyfile(src_file, dst_file)

    # Replace APP placeholder in _settings.py
    settings_path = DEST_DIR / "_settings.py"
    text = settings_path.read_text(encoding="utf-8")
    text = text.replace('APP = "<application_name>"', f'APP = "{app}"')
    settings_path.write_text(text, encoding="utf-8")


def main() -> None:
    app = detect_app_package()
    copy_templates(app)
    print(f"Installed modulith architecture tests. APP='{app}'.")
    print("Run: pytest -q tests/architecture")


if __name__ == "__main__":
    main()

# Start from repo root:
# python .codex/skills/modulith-architecture/bootstrap_modulith_arch_tests.py
# pytest -q tests/architecture
