import asyncio
import threading

import src.progena.llm.provider_context as provider_runtime
from src.progena.config import LLMConfig


class _Repository:
    def __init__(self) -> None:
        self.model = "gpt-4.1"
        self.revision = 1
        self.calls: list[str | None] = []

    def resolve(self, provider_id=None, model_override=None):
        self.calls.append(provider_id)
        return {
            "provider_id": provider_id or "provider-1",
            "provider_kind": "foundry",
            "base_url": "https://example.openai.azure.com/openai/v1/responses",
            "api_key": "test-key",
            "model_default": model_override or self.model,
            "revision": self.revision,
        }


class _BlockingRepository(_Repository):
    def __init__(self) -> None:
        super().__init__()
        self.first_resolution_started = threading.Event()
        self.release_first_resolution = threading.Event()

    def resolve(self, provider_id=None, model_override=None):
        self.calls.append(provider_id)
        call_number = len(self.calls)
        if call_number == 1:
            self.first_resolution_started.set()
            self.release_first_resolution.wait()
        return {
            "provider_id": provider_id or "provider-1",
            "provider_kind": "foundry",
            "base_url": "https://example.openai.azure.com/openai/v1/responses",
            "api_key": "test-key",
            "model_default": model_override or f"model-{call_number}",
            "revision": call_number,
        }


async def test_provider_turn_keeps_connection_snapshot_until_next_turn(monkeypatch):
    repository = _Repository()
    monkeypatch.setattr(provider_runtime, "get_provider_repository", lambda: repository)

    first_turn = await provider_runtime.ProviderTurn.create(LLMConfig(), session_id="thread-1")
    first = await first_turn.resolve()

    repository.model = "gpt-5.1"
    repository.revision = 2
    still_first = await first_turn.resolve()
    next_turn = await provider_runtime.ProviderTurn.create(LLMConfig(), session_id="thread-1")
    next_config = await next_turn.resolve()

    assert still_first.model_default == first.model_default == "gpt-4.1"
    assert still_first.revision == first.revision == 1
    assert next_config.model_default == "gpt-5.1"
    assert next_config.revision == 2


async def test_default_resolution_aliases_its_canonical_provider_snapshot(monkeypatch):
    repository = _Repository()
    monkeypatch.setattr(provider_runtime, "get_provider_repository", lambda: repository)

    turn = await provider_runtime.ProviderTurn.create(LLMConfig(), session_id="thread-1")
    repository.model = "gpt-5.1"
    repository.revision = 2

    explicit = await turn.resolve(provider_id="provider-1")

    assert repository.calls == [None]
    assert explicit.model_default == "gpt-4.1"
    assert explicit.revision == 1


async def test_concurrent_default_then_explicit_resolution_shares_snapshot(monkeypatch):
    repository = _BlockingRepository()
    monkeypatch.setattr(provider_runtime, "get_provider_repository", lambda: repository)
    turn = await provider_runtime.ProviderTurn.create(
        LLMConfig(), session_id="thread-1", resolve_default=False
    )
    tasks = [asyncio.create_task(turn.resolve())]
    try:
        await asyncio.wait_for(
            asyncio.to_thread(repository.first_resolution_started.wait), timeout=2
        )
        tasks.append(asyncio.create_task(turn.resolve(provider_id="provider-1")))
        await asyncio.sleep(0)
        assert not tasks[1].done()
    finally:
        repository.release_first_resolution.set()
        resolved = await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)

    assert repository.calls == [None]
    assert {config.revision for config in resolved} == {1}
    assert {config.model_default for config in resolved} == {"model-1"}


async def test_concurrent_explicit_then_default_resolution_keeps_first_snapshot(monkeypatch):
    repository = _BlockingRepository()
    monkeypatch.setattr(provider_runtime, "get_provider_repository", lambda: repository)
    turn = await provider_runtime.ProviderTurn.create(
        LLMConfig(), session_id="thread-1", resolve_default=False
    )
    tasks = [asyncio.create_task(turn.resolve(provider_id="provider-1"))]
    try:
        await asyncio.wait_for(
            asyncio.to_thread(repository.first_resolution_started.wait), timeout=2
        )
        tasks.append(asyncio.create_task(turn.resolve()))
        await asyncio.sleep(0)
        assert not tasks[1].done()
    finally:
        repository.release_first_resolution.set()
        resolved = await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)

    assert repository.calls == ["provider-1", None]
    assert {config.revision for config in resolved} == {1}
    assert {config.model_default for config in resolved} == {"model-1"}


async def test_go_model_override_is_restricted_to_responses_models(monkeypatch):
    repository = _Repository()

    original_resolve = repository.resolve

    def resolve(provider_id=None, model_override=None):
        record = original_resolve(provider_id, model_override)
        record["provider_kind"] = "opencode_go"
        return record

    repository.resolve = resolve
    monkeypatch.setattr(provider_runtime, "get_provider_repository", lambda: repository)
    turn = await provider_runtime.ProviderTurn.create(LLMConfig(), session_id="thread-1")

    try:
        await turn.resolve(model_override="not-a-go-model")
    except provider_runtime.ProviderResolutionError:
        pass
    else:
        raise AssertionError("unsupported OpenCode Go model must be rejected")
