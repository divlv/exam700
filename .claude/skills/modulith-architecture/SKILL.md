---
name: modulith-architecture
description: Use only when explicitly asked to install or update Python modulith architecture tests under tests/architecture, or when fixing failures in those tests.
---

# Skill: Modulith Architecture (Python)

## Trigger policy (mandatory)
- Do not run automatically at session start.
- Only proceed if:
  1) the user asks to install or update modulith architecture tests, or
  2) the user reports failures in tests/architecture (boundaries or cycles).
- If none of the above is true:
  - do not run commands
  - do not modify files
  - do not attempt repository layout detection

## Goal
Keep the codebase modular inside a single deployable app by enforcing strict module boundaries with automated architecture tests.

## Canonical module layout
- Path: `api/modules/<module_name>/`
- Required: `api.py` (public surface)
- Optional: `internal/` folder for private implementation details

## Architecture rules
1) Cross-module imports: ONLY `modules.<module>.api`
2) Forbidden: cross-module importing `modules.<module>.<anything_except_api>`
3) Forbidden: importing any other module's `internal` (direct or indirect)
4) No import cycles between modules
5) Module public API should expose DTOs and functions intended for cross-module usage

## Required checks
Run:
- `pytest -q tests/architecture`

If any check fails:
- Fix code to comply with the rules
- Never bypass boundaries for speed
- Prefer creating or extending a target module public API in `api.py`

## Optional allowed dependency manifest
If `tests/architecture/manifests/allowed_deps.toml` exists:
- Only the dependencies listed there are allowed
- When adding a new dependency:
  - update the manifest
  - add a short comment why it is needed
  - keep dependencies minimal

## When creating a new module
1) Create folder `api/modules/<new_module>/`
2) Create `api.py` with minimal public functions + DTOs
3) Keep implementation private (domain/services/repo/internal)
4) Ensure no cross-imports bypass `api.py`
5) Run architecture tests

## When modifying an existing module
- Prefer adding new API functions/DTOs to `api.py`
- Avoid exposing internal domain entities directly
- Validate inputs at the boundary (DTO validation if used)

---

## Modulith Architecture Tests (Install + Keep In Sync)

### Intent
This skill must ensure the repository contains strict, self-discovering modulith architecture tests.
The agent MUST implement all steps directly (file operations and edits). Do NOT invoke bootstrap scripts.

### When to apply
Apply this procedure when:
- a change introduces/changes modules under `api/modules/`.

### Target structure (must exist after work)
- `tests/architecture/_settings.py`
- `tests/architecture/test_boundaries.py`
- `tests/architecture/test_cycles.py`

Also ensure:
- `tests/__init__.py`
- `tests/architecture/__init__.py`

### Source templates (skill assets)
Templates are stored under:
- `.codex/skills/modulith-architecture/templates/tests/architecture/`

Use the templates as a starting point, but the final installed tests MUST satisfy all strictness requirements below.

### Direct implementation steps (mandatory, idempotent)
Do not use caching in tests; no cache directories should be created.
The agent MUST do the following steps directly by creating/editing files:

1) Detect `<app>` package name
   - Locate `api/modules/`.
   - If exactly one candidate exists:
     - store detected name as `<app>`.
   - If multiple candidates exist:
     - fail with a clear error listing candidates.
   - If no candidate exists:
     - create a src-layout modulith skeleton directly, then continue:
       - determine `<app>` deterministically:
         - if `pyproject.toml` exists and contains `[project].name`, use it
         - else use the repository folder name
         - sanitize to a valid Python package name (lowercase, letters/digits/underscore)
         - if sanitization results in empty value, use `app`
       - create directories:
         - `api/`
         - `api/modules/`
       - ensure init files exist:
         - `api/__init__.py`
         - `api/modules/__init__.py`
       - note: module folders under `api/modules/<module>/` are created by feature work,
         but architecture tests will require that any module folder that exists must contain `api.py`.

2) Create the test directories and init files
   - Create `tests/architecture/` if missing.
   - Ensure `tests/__init__.py` exists.
   - Ensure `tests/architecture/__init__.py` exists.

3) Install or update architecture tests
   - Create/update:
     - `tests/architecture/_settings.py`
     - `tests/architecture/test_boundaries.py`
     - `tests/architecture/test_cycles.py`
   - Replace `APP = "<application_name>"` with `APP = "<app>"`.

4) Make tests strict (avoid silent pass)
   The installed tests MUST:
   - Fail if `APP` is not configured (placeholder still present).
   - Fail if `api/modules/` does not exist (avoid empty scans passing).
   - Fail if no modules are discovered under `api/modules/` (likely misconfiguration).
   - Fail if any module directory under `api/modules/*` is missing `api.py`.

5) Enforce boundary rules (imports)
   The boundaries test MUST:
   - Allow cross-module imports ONLY via `modules.<module>.api`.
   - Forbid cross-module importing anything from another module that is not `api`.
   - Forbid importing any other module's `internal` (direct or indirect).
   - Detect and validate relative imports (`from ..x import y`) by resolving them to absolute module paths before enforcing the rules.

6) Enforce "no cycles"
   The cycles test MUST:
   - Detect cycles among `modules.*` packages.
   - Fail with a readable cycle chain when found.
   - If cycle checker dependency is missing, fail with a clear message instructing how to install it.

7) Ensure dev dependencies
   Ensure the repo includes dev dependencies required by these tests:
   - `pytest`
   - `grimp`

   Use the repo’s existing dependency management approach (pyproject.toml, requirements-dev.txt, etc.).
   Do NOT introduce a new dependency management tool.

8) Verification (must run)
   Run and ensure it passes:
   - `pytest -q tests/architecture`

   If it fails:
   - Fix the application code to comply.
   - Do NOT weaken tests to make them pass.

### Non-goals
- Do not hardcode a list of modules in tests.
- Adding a new module must not require changing tests.
- Do not bypass modulith rules for speed.
