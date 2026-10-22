"""The routine graphs the Playwright specs seed must stay valid routine graphs.

The specs insert them straight into the store (tests/frontend/helpers/routineFixture.mjs),
so nothing else validates them; a schema change that invalidates a fixture
would otherwise surface as an unexplained canvas failure in the e2e suite.
"""

import json
from pathlib import Path

import pytest

from src.progenitor.routines.graph import validate_routine_graph_payload

FIXTURES_PATH = Path(__file__).resolve().parents[2] / "tests/frontend/fixtures/routine-graphs.json"
FIXTURES = json.loads(FIXTURES_PATH.read_text())


@pytest.mark.parametrize("fixture_id", sorted(FIXTURES))
def test_e2e_routine_fixture_is_a_valid_graph(fixture_id: str) -> None:
    graph = FIXTURES[fixture_id]
    assert graph["id"] == fixture_id
    validate_routine_graph_payload(graph)
