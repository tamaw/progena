"""
Stream envelope helpers shared between the runtime and its HTTP chat endpoints.

Despite the package name (kept for now to avoid churning import paths), this
no longer contains a transport: chat and events are served over HTTP + SSE by
``app.progenitor.runtime.http_chat``. What remains here is transport-agnostic:
converting native DeepAgent/LangGraph stream items into normalized envelopes
(``deepagent_stream.py``, ``stream_events.py``, ``stream_payloads.py``).
"""
