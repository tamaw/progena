# Changelog

All notable changes to the Progenitor project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Mock LLM Client** (EN-004): Demo mode with `PROGENITOR_MOCK_LLM=true` environment variable
- **Tiktoken Integration**: Accurate token counting for OpenAI models with graceful fallback
- **Tokenizer Module**: New `app/progenitor/llm/tokenizer.py` with `count_tokens()`, `count_message_tokens()`, `truncate_to_tokens()` functions
- **Pre-commit Configuration**: Automated linting and formatting with ruff, black, and mypy

### Changed
- **Conversation Compressor**: Now uses tiktoken for accurate token estimation
- **Exception Handling**: Added DEBUG logging to previously silent exception handlers (CQ-009)

### Fixed
- Telemetry exception handlers now log at DEBUG level instead of silently passing

## [0.6.0] - 2026-01-29

### Added
- **Sprint 5: Chat UI** - Complete web interface for chat interactions
  - Real-time message streaming with token counter
  - Markdown rendering with syntax highlighting
  - Conversation management (export, clear, keyboard shortcuts)
  - Visual thinking indicators and sub-agent monitoring
  - Dark/light theme support
  - Mobile-responsive design
  - Accessibility features (ARIA labels, keyboard navigation)
  - Metrics dashboard for response times
  - 14 new JavaScript modules

### Changed
- Chat server now serves static UI files from `/ui/` endpoint
- API responses include thinking indicators via metadata field

## [0.5.0] - 2026-01-28

### Added
- **Sprint 4: IPC Protocol** - Length-prefixed JSON protocol between chat_server and progenitor
  - Bidirectional async streaming
  - Heartbeat and health monitoring
  - Automatic reconnection with exponential backoff
  - Request ID correlation across components

### Fixed
- CR-001: Subprocess IPC fragility - Added heartbeat monitoring and auto-restart
- CR-002: Missing input validation on WebSocket/streaming endpoints
- CR-003: Git worktree cleanup race condition

## [0.4.0] - 2026-01-27

### Added
- **Sprint 3: Observability** - Langfuse integration for LLM tracing
  - Telemetry decorators for automatic instrumentation
  - Metrics collection (counters, histograms, gauges)
  - JSONL export for log aggregation
  - Request ID correlation headers

### Changed
- All nodes now automatically instrumented with telemetry
- LLM calls tracked with token usage metrics

## [0.3.0] - 2026-01-26

### Added
- **Sprint 2: Reliability** - Production-ready infrastructure
  - Bounded input queues to prevent memory exhaustion
  - Rate limiting with per-client tracking
  - Graceful shutdown with configurable timeout
  - Docker health checks and resource limits
  - Secret redaction in logs

### Security
- SEC-001: Command injection protection with allowlist
- SEC-002: Secret redaction pattern for API keys

## [0.2.0] - 2026-01-25

### Added
- **Sprint 1: Architecture** - Core dual-loop system
  - LangGraph-based workflow engine
  - Composable state types with TypedDict
  - Dependency injection for nodes
  - Ideal state refinement loop
  - Tool registry with semantic search

### Changed
- Migrated from simple chain to dual-loop architecture
- State management now uses immutable updates

## [0.1.0] - 2026-01-24

### Added
- Initial project setup
- Basic chat completion with OpenAI/Azure OpenAI
- FastAPI chat server with OpenAI-compatible API
- Pydantic configuration management
- Basic test suite

---

## Categories

- **Added**: New features
- **Changed**: Changes to existing functionality  
- **Deprecated**: Features to be removed in future versions
- **Removed**: Features removed in this release
- **Fixed**: Bug fixes
- **Security**: Security-related changes

[Unreleased]: https://github.com/your-org/progenitor/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/your-org/progenitor/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/your-org/progenitor/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/your-org/progenitor/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/your-org/progenitor/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/your-org/progenitor/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/your-org/progenitor/releases/tag/v0.1.0
