from app.progenitor.nodes.chat import _get_system_tool_definitions


def test_schedule_lifecycle_tools_are_exposed_with_structured_parameters():
    tool_defs = _get_system_tool_definitions()
    by_name = {tool["function"]["name"]: tool for tool in tool_defs}

    assert "delete_schedule" in by_name
    assert "update_schedule" in by_name
    assert "pause_schedule" in by_name
    assert "resume_schedule" in by_name

    delete_params = by_name["delete_schedule"]["function"]["parameters"]
    assert delete_params["required"] == ["schedule_id"]
    assert "schedule_id" in delete_params["properties"]

    update_params = by_name["update_schedule"]["function"]["parameters"]
    assert update_params["required"] == ["schedule_id"]
    assert "cron_expression" in update_params["properties"]
