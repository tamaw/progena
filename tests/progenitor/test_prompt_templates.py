"""Tests for prompt template parsing, rendering, and type resolution."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.progenitor.document_types import BUILTIN_DOCUMENT_TYPES
from app.progenitor.prompts.templates import (
    PromptTemplate,
    parse_prompt_template,
    prompt_relative_path,
    render_prompt_template,
    resolve_type_ref,
)
from app.progenitor.structured import validate_against_schema
from app.progenitor.workflows import default_workflow_definition, derive_workflow_port_schemas


class TestParsePromptTemplate:
    def test_no_frontmatter(self):
        text = "Hello, world!"
        pt = parse_prompt_template(text)
        assert pt.title is None
        assert pt.description is None
        assert pt.inputs == {}
        assert pt.output_schema is None
        assert pt.system is None
        assert pt.body == "Hello, world!"
        assert pt.placeholders == []
        assert pt.roots == []

    def test_full_frontmatter(self):
        text = (
            "---\n"
            "title: Card moved\n"
            "description: Notify about a card move\n"
            "inputs:\n"
            "  card: kanban_card\n"
            "output_schema: kanban_card\n"
            "system: You are terse.\n"
            "---\n"
            'Card "{card.title}" moved to {card.column_id}.\n'
        )
        pt = parse_prompt_template(text)
        assert pt.title == "Card moved"
        assert pt.description == "Notify about a card move"
        assert pt.inputs == {"card": "kanban_card"}
        assert pt.output_schema == "kanban_card"
        assert pt.system == "You are terse."
        assert pt.body == 'Card "{card.title}" moved to {card.column_id}.\n'
        assert pt.placeholders == ["card.title", "card.column_id"]
        assert pt.roots == ["card"]

    def test_unknown_front_matter_key_rejected(self):
        text = "---\nfoo: bar\n---\nBody\n"
        with pytest.raises(ValueError, match="unknown front matter key"):
            parse_prompt_template(text)

    def test_a_longer_dash_rule_is_body_text_not_front_matter(self):
        text = "----\nExplain {topic}\n"
        pt = parse_prompt_template(text)
        assert pt.body == text
        assert pt.roots == ["topic"]

    def test_inputs_key_not_used_rejected(self):
        text = "---\ninputs:\n  card: kanban_card\n  unused: string\n---\nCard {card.title}\n"
        with pytest.raises(ValueError, match="inputs.*key.*not used"):
            parse_prompt_template(text)

    def test_dotted_placeholders(self):
        text = "{a.b} and {a.c.d} plus {x.y}\n"
        pt = parse_prompt_template(text)
        assert pt.placeholders == ["a.b", "a.c.d", "x.y"]
        assert pt.roots == ["a", "x"]

    def test_json_braces_left_literal(self):
        text = 'Example: {"a": 1} and [{"b": 2}]\n'
        pt = parse_prompt_template(text)
        assert pt.placeholders == []
        assert pt.roots == []

    def test_thread_id_reserved_not_a_root(self):
        text = "Thread: {thread_id}\n"
        pt = parse_prompt_template(text)
        assert pt.placeholders == ["thread_id"]
        assert pt.roots == []

    def test_frontmatter_only_no_body(self):
        text = "---\ntitle: Title\n---\n"
        pt = parse_prompt_template(text)
        assert pt.title == "Title"
        assert pt.body == ""

    def test_malformed_yaml_rejected(self):
        text = "---\n{ bad yaml\n---\nBody\n"
        with pytest.raises(ValueError, match="invalid YAML frontmatter"):
            parse_prompt_template(text)

    def test_unclosed_frontmatter_rejected(self):
        text = "---\ntitle: Title\nBody\n"
        with pytest.raises(ValueError, match="never closed"):
            parse_prompt_template(text)

    def test_non_mapping_frontmatter_rejected(self):
        text = "---\n- a\n- b\n---\nBody\n"
        with pytest.raises(ValueError, match="mapping"):
            parse_prompt_template(text)

    def test_inputs_must_be_mapping(self):
        text = "---\ninputs: not_a_map\n---\n{not_a_map}\n"
        with pytest.raises(ValueError, match="inputs.*mapping"):
            parse_prompt_template(text)

    def test_output_schema_must_be_string_or_dict(self):
        text = "---\noutput_schema: 123\n---\nBody\n"
        with pytest.raises(ValueError, match="output_schema"):
            parse_prompt_template(text)

    def test_system_must_be_string(self):
        text = "---\nsystem: 123\n---\nBody\n"
        with pytest.raises(ValueError, match="system"):
            parse_prompt_template(text)

    def test_inline_schema_in_inputs(self):
        text = (
            "---\ninputs:\n"
            "  data:\n"
            "    type: object\n"
            "    properties:\n"
            "      name:\n"
            "        type: string\n"
            "---\n"
            "Name: {data.name}\n"
        )
        pt = parse_prompt_template(text)
        assert pt.inputs == {"data": {"type": "object", "properties": {"name": {"type": "string"}}}}
        assert pt.roots == ["data"]


class TestRenderPromptTemplate:
    def test_simple_replacement(self):
        pt = PromptTemplate(
            title=None,
            description=None,
            inputs={},
            output_schema=None,
            system=None,
            body="Hello, {name}!",
            placeholders=["name"],
            roots=["name"],
        )
        assert render_prompt_template(pt, {"name": "World"}) == "Hello, World!"

    def test_dotted_replacement(self):
        pt = PromptTemplate(
            title=None,
            description=None,
            inputs={},
            output_schema=None,
            system=None,
            body="{card.title} in {card.column_id}",
            placeholders=["card.title", "card.column_id"],
            roots=["card"],
        )
        values = {"card": {"title": "Foo", "column_id": "done"}}
        assert render_prompt_template(pt, values) == "Foo in done"

    def test_non_string_json_dumps(self):
        pt = PromptTemplate(
            title=None,
            description=None,
            inputs={},
            output_schema=None,
            system=None,
            body="Data: {obj}",
            placeholders=["obj"],
            roots=["obj"],
        )
        result = render_prompt_template(pt, {"obj": {"b": 2, "a": 1}})
        assert result == 'Data: {\n  "a": 1,\n  "b": 2\n}'

    def test_missing_value_raises(self):
        pt = PromptTemplate(
            title=None,
            description=None,
            inputs={},
            output_schema=None,
            system=None,
            body="{missing}",
            placeholders=["missing"],
            roots=["missing"],
        )
        with pytest.raises(ValueError, match="missing value for {missing}"):
            render_prompt_template(pt, {})

    def test_missing_nested_value_raises(self):
        pt = PromptTemplate(
            title=None,
            description=None,
            inputs={},
            output_schema=None,
            system=None,
            body="{a.b}",
            placeholders=["a.b"],
            roots=["a"],
        )
        with pytest.raises(ValueError, match="missing value for {a.b}"):
            render_prompt_template(pt, {"a": {}})

    def test_system_prepended(self):
        pt = PromptTemplate(
            title=None,
            description=None,
            inputs={},
            output_schema=None,
            system="Be terse.",
            body="Hello.",
            placeholders=[],
            roots=[],
        )
        assert render_prompt_template(pt, {}) == "Be terse.\n\nHello."

    def test_list_value_json_dumps(self):
        pt = PromptTemplate(
            title=None,
            description=None,
            inputs={},
            output_schema=None,
            system=None,
            body="Items: {items}",
            placeholders=["items"],
            roots=["items"],
        )
        result = render_prompt_template(pt, {"items": [3, 1, 2]})
        assert result == "Items: [\n  3,\n  1,\n  2\n]"

    def test_thread_id_replacement(self):
        pt = PromptTemplate(
            title=None,
            description=None,
            inputs={},
            output_schema=None,
            system=None,
            body="Thread: {thread_id}",
            placeholders=["thread_id"],
            roots=[],
        )
        assert render_prompt_template(pt, {"thread_id": "abc123"}) == "Thread: abc123"


class TestResolveTypeRef:
    @pytest.fixture
    def document_types(self):
        return {dt.id: dt for dt in BUILTIN_DOCUMENT_TYPES}

    def test_document_type_id_titled_with_label(self, document_types):
        schema, _definitions = resolve_type_ref("kanban_card", document_types)
        assert schema.get("title") == "Kanban Card"
        assert schema.get("type") == "object"

    def test_schema_file_by_name(self, document_types):
        schema, _definitions = resolve_type_ref("schedule_entry", document_types)
        assert schema.get("type") == "object"
        assert "properties" in schema

    def test_inline_dict(self, document_types):
        inline = {"type": "string", "enum": ["a", "b"]}
        assert resolve_type_ref(inline, document_types) == (inline, {})

    def test_unknown_raises(self, document_types):
        with pytest.raises(ValueError, match="unknown type ref"):
            resolve_type_ref("no_such_thing_ever", document_types)

    def test_document_type_without_schema(self, document_types):
        # schedule has no schema_name
        schema, _definitions = resolve_type_ref("schedule", document_types)
        assert schema.get("title") == "Schedule"
        assert schema.get("type") == "object"

    def test_typed_input_keeps_definitions_resolvable_at_the_port_root(self, document_types):
        payload = default_workflow_definition()
        agent_id = next(i for i, n in payload["graph"]["nodes"].items() if n["kind"] == "agent")
        payload["graph"]["nodes"][agent_id]["template"] = (
            "---\ninputs:\n  flow: workflow\n---\nReview {flow.title} for {key}."
        )
        derived = derive_workflow_port_schemas(payload, document_types)
        input_schema = derived["graph"]["nodes"][agent_id]["input_schema"]
        flow = default_workflow_definition()
        assert flow["graph"]["edges"], "fixture must contain edges using #/$defs/edgeEndpoint"

        assert validate_against_schema({"key": "k", "flow": flow}, input_schema) is None

    def test_inline_input_with_a_body_reference_stays_resolvable_when_embedded(
        self, document_types
    ):
        payload = default_workflow_definition()
        agent_id = next(i for i, n in payload["graph"]["nodes"].items() if n["kind"] == "agent")
        payload["graph"]["nodes"][agent_id]["template"] = (
            "---\ninputs:\n  person:\n    type: object\n    properties:\n"
            "      name: {type: string}\n      alias: {$ref: '#/properties/name'}\n"
            "---\nGreet {person.name} for {key}."
        )
        derived = derive_workflow_port_schemas(payload, document_types)
        input_schema = derived["graph"]["nodes"][agent_id]["input_schema"]

        valid = {"key": "k", "person": {"name": "Ada", "alias": "A"}}
        assert validate_against_schema(valid, input_schema) is None
        invalid = {"key": "k", "person": {"name": "Ada", "alias": 7}}
        assert validate_against_schema(invalid, input_schema) is not None


class TestFrontMatterKeys:
    @pytest.mark.parametrize("front_matter", ["1: value", "title: t\n2: value", "true: x"])
    def test_non_string_keys_are_validation_errors(self, front_matter):
        with pytest.raises(ValueError, match="keys must be strings"):
            parse_prompt_template(f"---\n{front_matter}\n---\nBody\n")


class TestPromptRelativePath:
    @pytest.mark.parametrize("path", ["workflows/card.md", "a.MD"])
    def test_accepts_relative_markdown(self, path):
        assert prompt_relative_path(path) == path

    @pytest.mark.parametrize("path", ["/prompts/workflows/card.md", "../x.md", "x.txt", "dir/", ""])
    def test_rejects_everything_else(self, path):
        with pytest.raises(ValueError):
            prompt_relative_path(path)


class TestPromptDocumentDirectoryBackend:
    async def test_valid_write_persists(self):
        from app.progenitor.agent.state_backend import PromptDocumentDirectoryBackend

        class FakeSM(MagicMock):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._docs: dict[str, dict[str, str]] = {"prompt": {}}

            async def save_content_document(
                self, kind: str, relative_path: str, content: str
            ) -> None:
                self._docs[kind][relative_path] = content

        sm = FakeSM()
        backend = PromptDocumentDirectoryBackend(sm, "prompt")
        result = await backend.awrite("/test.md", "# Hello\n")
        assert result.error is None
        assert sm._docs["prompt"]["test.md"] == "# Hello\n"

    async def test_invalid_front_matter_rejected(self):
        from app.progenitor.agent.state_backend import PromptDocumentDirectoryBackend

        class FakeSM(MagicMock):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._docs: dict[str, dict[str, str]] = {"prompt": {}}

            async def save_content_document(
                self, kind: str, relative_path: str, content: str
            ) -> None:
                self._docs[kind][relative_path] = content

        sm = FakeSM()
        backend = PromptDocumentDirectoryBackend(sm, "prompt")
        result = await backend.awrite("/test.md", "---\nunknown: 1\n---\nBody\n")
        assert result.error is not None
        assert "unknown front matter key" in result.error
        assert sm._docs["prompt"] == {}

    async def test_non_markdown_path_rejected(self):
        from app.progenitor.agent.state_backend import PromptDocumentDirectoryBackend

        sm = MagicMock()
        backend = PromptDocumentDirectoryBackend(sm, "prompt")
        result = await backend.awrite("/example.txt", "Body\n")
        assert result.error is not None and ".md" in result.error
        sm.save_content_document.assert_not_called()


class TestEnsureContentDocumentsSeeded:
    async def test_adds_missing_key_without_touching_existing(self, tmp_path):
        from app.progenitor.content_documents import ensure_content_documents_seeded

        class FakeSM:
            def __init__(self):
                self._docs: dict[str, dict[str, str]] = {"prompt": {}}

            async def get_content_document(self, kind: str, relative_path: str) -> str | None:
                return self._docs[kind].get(relative_path)

            async def save_content_document(
                self, kind: str, relative_path: str, content: str
            ) -> None:
                self._docs[kind][relative_path] = content

        sm = FakeSM()
        sm._docs["prompt"]["existing.md"] = "edited content"

        source = tmp_path / "prompts"
        source.mkdir()
        (source / "existing.md").write_text("shipped content", encoding="utf-8")
        (source / "new.md").write_text("new content", encoding="utf-8")

        await ensure_content_documents_seeded(sm, "prompt", source)

        assert sm._docs["prompt"]["existing.md"] == "edited content"
        assert sm._docs["prompt"]["new.md"] == "new content"
