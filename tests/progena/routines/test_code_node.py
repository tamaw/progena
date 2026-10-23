"""Routine `code` nodes execute bounded, isolated QuickJS source."""

from __future__ import annotations

import asyncio

import pytest
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from langchain_core.messages import HumanMessage
from langgraph.errors import NodeTimeoutError
from quickjs_rs import JSError
from quickjs_rs import TimeoutError as QuickJSTimeoutError

from src.progena.routines.factory import compile_routine_graph
from src.progena.routines.graph import validate_routine_graph_payload


def _payload(*nodes: dict, edges: list[dict] | None = None, **extra: object) -> dict:
    return {
        "id": "quickjs-test",
        "name": "QuickJS test",
        "description": "",
        "version": "2.0.0",
        "entry": nodes[0]["id"],
        "nodes": list(nodes),
        "edges": edges or [],
        **extra,
    }


def _code_node(code: str, *, output_key: str = "result") -> dict:
    return {
        "id": "code",
        "kind": "code",
        "output_key": output_key,
        "config": {"language": "javascript", "code": code},
    }


@pytest.mark.asyncio
async def test_code_node_runs_inline_javascript_with_structured_input_and_output():
    graph = validate_routine_graph_payload(
        _payload(
            _code_node(
                """
                const subtotal = input.items.reduce((sum, item) => sum + item.price, 0);
                return { subtotal, requested: input.request };
                """
            ),
            {
                "id": "after",
                "kind": "eval",
                "output_key": "after",
                "config": {"expression": "result.subtotal + 1"},
            },
            edges=[{"from": "code", "to": "after", "level": "normal"}],
            input_schema={
                "type": "object",
                "properties": {"items": {"type": "array"}},
                "required": ["items"],
            },
        )
    )

    compiled = compile_routine_graph(graph, model=object())
    result = await compiled.ainvoke(
        {
            "messages": [HumanMessage(content="sum these")],
            "items": [{"price": 19}, {"price": 23}],
        }
    )

    assert result["data"]["result"] == {"subtotal": 42, "requested": "sum these"}
    assert result["data"]["after"] == 43


@pytest.mark.parametrize(
    "config",
    [
        {"code": "return 1;"},
        {"language": "python", "code": "return 1;"},
        {"language": "javascript", "code": " \n\t "},
        {"language": "javascript", "code": "return 1;", "code_file": "step.js"},
    ],
)
def test_code_node_rejects_invalid_configuration(config: dict[str, str]):
    payload = _payload(
        {
            "id": "code",
            "kind": "code",
            "output_key": "result",
            "config": config,
        }
    )

    with pytest.raises(ValueError, match="schema validation failed"):
        validate_routine_graph_payload(payload)


def test_code_filesystem_scopes_are_canonicalized_and_serialize_round_trip():
    graph = validate_routine_graph_payload(
        _payload(
            {
                **_code_node("return input;"),
                "config": {
                    "language": "javascript",
                    "code": "return input;",
                    "filesystem": {
                        "access": "read",
                        "paths": ["/artifacts//reports", "/shared/"],
                    },
                },
            }
        )
    )

    filesystem = graph.nodes[0].config.filesystem
    assert filesystem is not None
    assert filesystem.paths == ["/artifacts/reports/", "/shared/"]
    assert graph.model_dump(mode="json")["nodes"][0]["config"]["filesystem"] == {
        "access": "read",
        "paths": ["/artifacts/reports/", "/shared/"],
    }


@pytest.mark.parametrize(
    "path",
    [
        "",
        "artifacts/",
        "/etc/",
        "/artifacts/../shared/",
        "/artifacts\\reports/",
        "/artifacts/\x00",
    ],
)
def test_code_filesystem_rejects_unsafe_scopes(path: str):
    payload = _payload(
        {
            **_code_node("return input;"),
            "config": {
                "language": "javascript",
                "code": "return input;",
                "filesystem": {"access": "read", "paths": [path]},
            },
        }
    )

    with pytest.raises(ValueError):
        validate_routine_graph_payload(payload)


def test_code_filesystem_null_preserves_the_pure_code_configuration():
    graph = validate_routine_graph_payload(
        _payload(
            {
                **_code_node("return input;"),
                "config": {
                    "language": "javascript",
                    "code": "return input;",
                    "filesystem": None,
                },
            }
        )
    )

    assert graph.nodes[0].config.filesystem is None


@pytest.mark.asyncio
async def test_code_node_has_fresh_globals_for_each_execution():
    graph = validate_routine_graph_payload(
        _payload(
            _code_node(
                """
                const previous = globalThis.leaked ?? null;
                globalThis.leaked = "only this invocation";
                input.nested.value = 999;
                return { previous, copied: input.nested.value };
                """
            ),
            input_schema={
                "type": "object",
                "properties": {"nested": {"type": "object"}},
                "required": ["nested"],
            },
        )
    )
    compiled = compile_routine_graph(graph, model=object())

    first = await compiled.ainvoke(
        {"messages": [HumanMessage(content="first")], "nested": {"value": 1}}
    )
    second = await compiled.ainvoke(
        {"messages": [HumanMessage(content="second")], "nested": {"value": 2}}
    )

    assert first["data"]["result"] == {"previous": None, "copied": 999}
    assert second["data"]["result"] == {"previous": None, "copied": 999}
    assert first["data"]["nested"] == {"value": 1}
    assert second["data"]["nested"] == {"value": 2}


@pytest.mark.asyncio
async def test_code_node_propagates_javascript_errors_and_enforces_timeout():
    failing = validate_routine_graph_payload(_payload(_code_node('throw new Error("boom");')))
    with pytest.raises(JSError, match="boom"):
        await compile_routine_graph(failing, model=object()).ainvoke(
            {"messages": [HumanMessage(content="run")]}
        )

    unbounded = validate_routine_graph_payload(_payload(_code_node("while (true) {}")))
    with pytest.raises(QuickJSTimeoutError):
        await compile_routine_graph(unbounded, model=object()).ainvoke(
            {"messages": [HumanMessage(content="run")]}
        )


@pytest.mark.asyncio
async def test_code_node_rejects_oversized_result_inside_the_sandbox():
    graph = validate_routine_graph_payload(
        _payload(_code_node('return "x".repeat(1024 * 1024 + 1);'))
    )

    with pytest.raises(JSError, match="code result exceeds"):
        await compile_routine_graph(graph, model=object()).ainvoke(
            {"messages": [HumanMessage(content="run")]}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        "return undefined;",
        "return Infinity;",
        "return NaN;",
        "return 1n;",
        "return { callback() {} };",
        "const value = {}; value.self = value; return value;",
    ],
)
async def test_code_node_rejects_non_json_results(code: str):
    graph = validate_routine_graph_payload(_payload(_code_node(code)))

    with pytest.raises(JSError):
        await compile_routine_graph(graph, model=object()).ainvoke(
            {"messages": [HumanMessage(content="run")]}
        )


@pytest.mark.asyncio
async def test_code_node_preserves_json_keys_and_exposes_no_host_capabilities():
    graph = validate_routine_graph_payload(
        _payload(
            _code_node(
                """
                return {
                  own: Object.hasOwn(input.value, "__proto__"),
                  value: input.value.__proto__.answer,
                  host: [typeof tools, typeof task, typeof fetch,
                         typeof process, typeof require, typeof document]
                };
                """
            ),
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "object"}},
            },
        )
    )
    result = await compile_routine_graph(graph, model=object()).ainvoke(
        {
            "messages": [HumanMessage(content="inspect")],
            "value": {"__proto__": {"answer": 42}},
        }
    )
    assert result["data"]["result"] == {
        "own": True,
        "value": 42,
        "host": ["undefined"] * 6,
    }


@pytest.mark.asyncio
async def test_code_node_honors_a_shorter_graph_timeout():
    node = _code_node("const end = Date.now() + 250; while (Date.now() < end) {} return 42;")
    node["config"]["timeout"] = 0.02
    graph = validate_routine_graph_payload(_payload(node))
    with pytest.raises(NodeTimeoutError):
        await compile_routine_graph(graph, model=object()).ainvoke({})


@pytest.mark.asyncio
async def test_code_node_enforces_utf8_result_cap_after_guest_helper_mutation():
    graph = validate_routine_graph_payload(
        _payload(
            _code_node('String.prototype.charCodeAt.call = () => 0; return "é".repeat(600000);')
        )
    )
    with pytest.raises((JSError, ValueError)):
        await compile_routine_graph(graph, model=object()).ainvoke({})


def _filesystem_backend(tmp_path):
    artifacts = tmp_path / "artifacts"
    shared = tmp_path / "shared"
    artifacts.mkdir(parents=True)
    shared.mkdir(parents=True)
    return CompositeBackend(
        default=StateBackend(),
        routes={
            "/artifacts/": FilesystemBackend(root_dir=str(artifacts), virtual_mode=True),
            "/shared/": FilesystemBackend(root_dir=str(shared), virtual_mode=True),
        },
    )


def _filesystem_code_node(
    code: str,
    *,
    access: str = "read_write",
    paths: list[str] | None = None,
) -> dict:
    node = _code_node(code)
    node["config"]["filesystem"] = {
        "access": access,
        "paths": paths or ["/artifacts/", "/shared/"],
    }
    return node


@pytest.mark.asyncio
async def test_code_node_filesystem_tools_use_scoped_composite_backend(tmp_path):
    backend = _filesystem_backend(tmp_path)
    (tmp_path / "artifacts" / "source.txt").write_text("hello\n", encoding="utf-8")
    graph = validate_routine_graph_payload(
        _payload(
            _filesystem_code_node(
                """
                const source = await tools.readFile({ file_path: "/artifacts/source.txt" });
                const listing = await tools.ls({ path: "/artifacts/" });
                const files = await tools.glob({ pattern: "*.txt", path: "/artifacts/" });
                const hits = await tools.grep({ pattern: "hello", path: "/artifacts/" });
                await tools.writeFile({ file_path: "/artifacts/output.txt", content: source });
                await tools.editFile({
                  file_path: "/artifacts/output.txt",
                  old_string: "hello",
                  new_string: "updated"
                });
                await tools.copyFile({
                  source_path: "/artifacts/output.txt",
                  destination_path: "/shared/copied.txt"
                });
                await tools.moveFile({
                  source_path: "/shared/copied.txt",
                  destination_path: "/shared/moved.txt"
                });
                await tools.deleteFile({ file_path: "/artifacts/output.txt" });
                return {
                  source,
                  listed: listing.map((entry) => entry.path),
                  globbed: files.matches,
                  hits: hits.matches.length
                };
                """
            )
        )
    )

    result = await compile_routine_graph(graph, model=object(), filesystem_backend=backend).ainvoke(
        {}
    )

    assert result["data"]["result"] == {
        "source": "hello\n",
        "listed": ["/artifacts/source.txt"],
        "globbed": ["/artifacts/source.txt"],
        "hits": 1,
    }
    assert not (tmp_path / "artifacts" / "output.txt").exists()
    assert not (tmp_path / "shared" / "copied.txt").exists()
    assert (tmp_path / "shared" / "moved.txt").read_text(encoding="utf-8") == "updated\n"


@pytest.mark.asyncio
async def test_code_node_filesystem_rejects_scope_escapes_and_read_only_writes(tmp_path):
    backend = _filesystem_backend(tmp_path)
    (tmp_path / "artifacts" / "visible.txt").write_text("visible", encoding="utf-8")
    (tmp_path / "shared" / "secret.txt").write_text("secret", encoding="utf-8")

    readonly = validate_routine_graph_payload(
        _payload(
            _filesystem_code_node(
                """
                return {
                  content: await tools.readFile({ file_path: "/artifacts/visible.txt" }),
                  writeType: typeof tools.writeFile
                };
                """,
                access="read",
                paths=["/artifacts/"],
            )
        )
    )
    result = await compile_routine_graph(
        readonly, model=object(), filesystem_backend=backend
    ).ainvoke({})
    assert result["data"]["result"] == {"content": "visible", "writeType": "undefined"}

    escaped = validate_routine_graph_payload(
        _payload(
            _filesystem_code_node(
                'return await tools.readFile({ file_path: "/artifacts/../shared/secret.txt" });',
                access="read",
                paths=["/artifacts/"],
            )
        )
    )
    with pytest.raises(JSError, match="path traversal"):
        await compile_routine_graph(escaped, model=object(), filesystem_backend=backend).ainvoke({})

    (tmp_path / "artifacts" / "binary.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    binary = validate_routine_graph_payload(
        _payload(
            _filesystem_code_node(
                'return await tools.readFile({ file_path: "/artifacts/binary.png" });',
                access="read",
                paths=["/artifacts/"],
            )
        )
    )
    with pytest.raises(JSError, match="binary files are not supported"):
        await compile_routine_graph(binary, model=object(), filesystem_backend=backend).ainvoke({})


def test_code_node_filesystem_rejects_missing_backend_and_composite_default_fallback(tmp_path):
    backend = CompositeBackend(
        default=StateBackend(),
        routes={
            "/artifacts/": FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True),
        },
    )
    graph = validate_routine_graph_payload(
        _payload(
            _filesystem_code_node(
                "return 1;",
                access="read",
                paths=["/shared/"],
            )
        )
    )
    with pytest.raises(ValueError, match="no filesystem backend"):
        compile_routine_graph(graph, model=object())

    with pytest.raises(ValueError, match="scope is not mounted"):
        compile_routine_graph(graph, model=object(), filesystem_backend=backend)


@pytest.mark.asyncio
async def test_code_node_filesystem_errors_are_catchable_and_preserve_no_overwrite(tmp_path):
    backend = _filesystem_backend(tmp_path)
    (tmp_path / "artifacts" / "source.txt").write_text("source", encoding="utf-8")
    destination = tmp_path / "artifacts" / "destination.txt"
    shared_destination = tmp_path / "shared" / "destination.txt"
    destination.write_text("existing", encoding="utf-8")
    shared_destination.write_text("shared existing", encoding="utf-8")

    caught = validate_routine_graph_payload(
        _payload(
            _filesystem_code_node(
                """
                async function errorMessage(operation) {
                  try {
                    await operation();
                  } catch (error) {
                    return error.message;
                  }
                }
                return {
                  missing: await errorMessage(() =>
                    tools.readFile({ file_path: "/artifacts/missing.txt" })
                  ),
                  copy: await errorMessage(() => tools.copyFile({
                    source_path: "/artifacts/source.txt",
                    destination_path: "/artifacts/destination.txt"
                  })),
                  write: await errorMessage(() => tools.writeFile({
                    file_path: "/artifacts/destination.txt",
                    content: "replacement"
                  })),
                  writeShared: await errorMessage(() => tools.writeFile({
                    file_path: "/shared/destination.txt",
                    content: "replacement"
                  }))
                };
                """
            )
        )
    )
    result = await compile_routine_graph(
        caught, model=object(), filesystem_backend=backend
    ).ainvoke({})

    assert result["data"]["result"] == {
        "missing": "readFile: File '/missing.txt' not found",
        "copy": "copyFile: destination already exists: /artifacts/destination.txt",
        "write": "writeFile: destination already exists: /artifacts/destination.txt",
        "writeShared": "writeFile: destination already exists: /shared/destination.txt",
    }
    assert destination.read_text(encoding="utf-8") == "existing"
    assert shared_destination.read_text(encoding="utf-8") == "shared existing"

    uncaught = validate_routine_graph_payload(
        _payload(
            _filesystem_code_node(
                'return await tools.readFile({ file_path: "/artifacts/missing.txt" });'
            )
        )
    )
    with pytest.raises(JSError, match=r"readFile: File '/missing\.txt' not found"):
        await compile_routine_graph(uncaught, model=object(), filesystem_backend=backend).ainvoke(
            {}
        )


class _UnexpectedFilesystemFailureBackend:
    def __init__(self, backend):
        self._backend = backend

    async def aread(self, *_args, **_kwargs):
        raise RuntimeError("internal filesystem failure")

    def __getattr__(self, name: str):
        return getattr(self._backend, name)


@pytest.mark.asyncio
async def test_code_node_sanitizes_unexpected_filesystem_host_failures(tmp_path):
    backend = _UnexpectedFilesystemFailureBackend(_filesystem_backend(tmp_path))
    graph = validate_routine_graph_payload(
        _payload(
            _filesystem_code_node(
                """
                try {
                  await tools.readFile({ file_path: "/artifacts/anything.txt" });
                } catch (error) {
                  return String(error);
                }
                """
            )
        )
    )

    result = await compile_routine_graph(graph, model=object(), filesystem_backend=backend).ainvoke(
        {}
    )

    assert result["data"]["result"] == "HostError: Host function failed"


class _FailingSourceDeleteBackend:
    def __init__(self, backend, source_path: str):
        self._backend = backend
        self._source_path = source_path

    async def adelete(self, path: str):
        if path == self._source_path:
            raise RuntimeError("source delete failed")
        return await self._backend.adelete(path)

    def __getattr__(self, name: str):
        return getattr(self._backend, name)


@pytest.mark.asyncio
async def test_code_node_filesystem_move_rolls_back_destination_when_source_delete_fails(tmp_path):
    backend = _filesystem_backend(tmp_path)
    source_file = tmp_path / "artifacts" / "source.txt"
    source_file.write_text("source", encoding="utf-8")
    failing_backend = _FailingSourceDeleteBackend(backend, "/artifacts/source.txt")
    graph = validate_routine_graph_payload(
        _payload(
            _filesystem_code_node(
                """
                return await tools.moveFile({
                  source_path: "/artifacts/source.txt",
                  destination_path: "/artifacts/destination.txt"
                });
                """
            )
        )
    )

    with pytest.raises(JSError, match="destination rolled back"):
        await compile_routine_graph(
            graph, model=object(), filesystem_backend=failing_backend
        ).ainvoke({})
    assert source_file.read_text(encoding="utf-8") == "source"
    assert not (tmp_path / "artifacts" / "destination.txt").exists()


class _BlockingFilesystemBackend:
    """Backend wrapper that only blocks read calls after the QuickJS host starts."""

    def __init__(self, backend):
        self._backend = backend
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def aread(self, *_args, **_kwargs):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise

    def __getattr__(self, name: str):
        return getattr(self._backend, name)


@pytest.mark.asyncio
async def test_code_node_filesystem_host_call_cancels_on_node_timeout_and_task_cancellation(
    tmp_path,
):
    source = 'return await tools.readFile({ file_path: "/artifacts/slow.txt" });'
    node = _filesystem_code_node(source, paths=["/artifacts/"])
    node["config"]["timeout"] = 0.02
    timed_backend = _BlockingFilesystemBackend(_filesystem_backend(tmp_path / "timed"))
    timed_graph = validate_routine_graph_payload(_payload(node))

    with pytest.raises(NodeTimeoutError):
        await compile_routine_graph(
            timed_graph, model=object(), filesystem_backend=timed_backend
        ).ainvoke({})
    assert timed_backend.started.is_set()
    await asyncio.wait_for(timed_backend.cancelled.wait(), timeout=1)

    cancelled_backend = _BlockingFilesystemBackend(_filesystem_backend(tmp_path / "cancelled"))
    cancelled_graph = validate_routine_graph_payload(
        _payload(_filesystem_code_node(source, paths=["/artifacts/"]))
    )
    invocation = asyncio.create_task(
        compile_routine_graph(
            cancelled_graph, model=object(), filesystem_backend=cancelled_backend
        ).ainvoke({})
    )
    await asyncio.wait_for(cancelled_backend.started.wait(), timeout=1)
    invocation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await invocation
    await asyncio.wait_for(cancelled_backend.cancelled.wait(), timeout=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        'return await tools.readFile({file_path: "/shared/allowed/link/secret.txt"});',
        'await tools.writeFile({file_path: "/shared/allowed/link/new.txt", content: "escape"}); return {};',
    ],
)
async def test_filesystem_subtree_scope_rejects_symlink_escape(tmp_path, code):
    backend = _filesystem_backend(tmp_path)
    allowed = tmp_path / "shared" / "allowed"
    private = tmp_path / "shared" / "private"
    allowed.mkdir()
    private.mkdir()
    (private / "secret.txt").write_text("private", encoding="utf-8")
    (allowed / "link").symlink_to(private, target_is_directory=True)
    graph = validate_routine_graph_payload(
        _payload(_filesystem_code_node(code, paths=["/shared/allowed/"]))
    )
    with pytest.raises(JSError):
        await compile_routine_graph(graph, model=object(), filesystem_backend=backend).ainvoke({})
    assert not (private / "new.txt").exists()
