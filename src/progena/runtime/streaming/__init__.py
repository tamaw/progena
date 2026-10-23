"""
Stream envelope helpers shared between the runtime and its HTTP chat endpoints.

Chat and events are served over HTTP + SSE by
``src.progena.runtime.http.chat``; this package holds the
transport-agnostic pieces that feed it: converting native DeepAgent/LangGraph
stream items into normalized envelopes (``deepagent_stream.py``,
``stream_events.py``, ``stream_payloads.py``).
"""
