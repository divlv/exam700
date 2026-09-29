# tests/architecture/test_cycles.py
# All comments are in English.

from __future__ import annotations

import pytest

from tests.architecture._settings import APP

try:
    from grimp import ImportGraph
except ImportError:  # pragma: no cover
    ImportGraph = None


def test_no_import_cycles() -> None:
    if ImportGraph is None:
        pytest.fail("Missing dependency 'grimp'. Install it in dev dependencies to run cycle checks.")

    graph = ImportGraph()
    graph.add_package(f"{APP}.modules")

    cycles = graph.find_cycles()
    if cycles:
        pretty = "\n".join(" -> ".join(c) for c in cycles)
        raise AssertionError("Import cycles detected:\n" + pretty)
