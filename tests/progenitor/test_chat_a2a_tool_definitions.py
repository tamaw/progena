from app.progenitor.nodes.chat import _get_a2a_agent_tool_definitions


class _FakeRegistry:
    def get_agents(self):
        return [
            {"name": "observe_agent", "description": "Observes logs/state"},
            {"name": "hello_world_agent", "description": "Hello"},
        ]


def test_chat_exposes_a2a_agents_as_tools() -> None:
    tools = _get_a2a_agent_tool_definitions(_FakeRegistry())
    names = {t["function"]["name"] for t in tools}
    assert "observe_agent" in names
    assert "hello_world_agent" in names

