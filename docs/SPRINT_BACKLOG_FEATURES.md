# Sprint Backlog - Features from Text Generation WebUI

> **Source Repository:** text-generation-webui (oobabooga)
> **Target Project:** Progenitor Agent
> **Created:** 2026-01-29

---

## Overview

This backlog captures features extracted from the text-generation-webui project that can enhance the Progenitor agent. Features are prioritized based on relevance to an autonomous AI agent system.

---

## 🔴 Priority 1: High Impact / Core Features

### 1. Extension/Plugin System
**Source:** `modules/extensions.py`, `extensions/*/script.py`

**Description:** A modular plugin architecture that allows dynamically loading, configuring, and composing extensions at runtime.

**Key Features:**
- Hot-loadable extensions via `--extensions` flag
- Extension lifecycle hooks: `setup()`, `ui()`, `custom_css()`, `custom_js()`
- Input/output modifier chain: `input_modifier()`, `output_modifier()`, `state_modifier()`
- Extension prioritization and ordering
- Per-extension settings via `params` dict
- Extension requirements auto-detection

**Progenitor Application:**
- Enable dynamic tool loading/unloading
- Support third-party agent capabilities
- Create a marketplace of agent skills
- Allow per-task extension composition

**Story Points:** 13

---

### 2. OpenAI-Compatible API Layer
**Source:** `extensions/openai/script.py`, `docs/12 - OpenAI API.md`

**Description:** Drop-in replacement API that mimics OpenAI's Chat Completions and Completions endpoints.

**Key Features:**
- `/v1/chat/completions` and `/v1/completions` endpoints
- SSE streaming support with semaphore-controlled concurrency
- API key authentication (`--api-key`)
- Admin key for privileged operations
- Tool calling support
- CORS middleware configuration
- Automatic host header validation

**Progenitor Application:**
- Make Progenitor available as a backend for any OpenAI-compatible client
- Enable integration with existing AI tooling ecosystem
- Support streaming responses to chat UI

**Story Points:** 8

---

### 3. Web Search Integration
**Source:** `modules/web_search.py`

**Description:** LLM-generated queries to search the internet and add context as attachments.

**Key Features:**
- DuckDuckGo search integration (no API key required)
- Parallel page download with ThreadPoolExecutor
- HTML to Markdown conversion using html2text
- Token-based content truncation with binary search
- Results attached as metadata to conversation history
- Configurable number of search pages

**Progenitor Application:**
- Enable agent to gather real-time information
- Ground responses in current facts
- Support research-intensive tasks

**Story Points:** 5

---

### 4. Streaming Generation with Callbacks
**Source:** `modules/callbacks.py`

**Description:** Transform blocking generation functions into lazy iterators for real-time streaming.

**Key Features:**
- `Iteratorize` class converts callback-based APIs to generators
- Queue-based communication between threads
- Graceful stop mechanism via `StopNowException`
- Sentinel-based iteration termination
- Thread-safe stop signaling via `shared.stop_everything`

**Progenitor Application:**
- Enable real-time token streaming to UI
- Support cancellation of long-running generations
- Improve perceived responsiveness

**Story Points:** 5

---

## 🟡 Priority 2: Medium Impact / Enhancement Features

### 5. Preset/Configuration System
**Source:** `modules/presets.py`, `modules/prompts.py`

**Description:** Save and load generation parameter presets from YAML files.

**Key Features:**
- Default preset with all sampling parameters
- YAML-based preset files in `user_data/presets/`
- Parameter validation and fallback to defaults
- Verbose logging of applied presets
- Portable mode with reduced sampler set

**Progenitor Application:**
- Save agent personas/configurations
- Quick switching between task-specific parameter sets
- User-customizable generation behavior

**Story Points:** 3

---

### 6. Model Loader Abstraction
**Source:** `modules/models.py`, `modules/loaders.py`

**Description:** Unified interface for loading models from multiple backends.

**Key Features:**
- Loader function map pattern (`load_func_map`)
- Auto-detection of appropriate loader
- Metadata-driven configuration
- Support for: llama.cpp, Transformers, ExLlamaV2/V3, TensorRT-LLM
- Model settings persistence
- Graceful error handling with fallback

**Progenitor Application:**
- Abstract LLM backend switching
- Enable local model support alongside cloud APIs
- Support model comparison/evaluation workflows

**Story Points:** 8

---

### 7. Conversation History with Metadata
**Source:** `modules/chat.py`

**Description:** Rich conversation history with per-message metadata tracking.

**Key Features:**
- Separate `internal` vs `visible` history tracks
- Metadata dictionary keyed by `{role}_{index}`
- Timestamp tracking for messages
- Attachment system for files/search results
- Message versioning and branching support
- Jinja2 template-based prompt formatting

**Progenitor Application:**
- Track reasoning traces per message
- Store tool call metadata
- Enable conversation replay/debugging
- Support multi-turn context management

**Story Points:** 8

---

### 8. File Attachment System
**Source:** `modules/chat.py`, `modules/web_search.py`

**Description:** Upload and attach text files, PDFs, and documents to messages.

**Key Features:**
- Attachments stored in message metadata
- Support for text, HTML, and document types
- Content truncation to fit context window
- URL-based attachments from web search
- Automatic content extraction

**Progenitor Application:**
- Enable document-grounded conversations
- Support long-form input processing
- File-based task input

**Story Points:** 5

---

### 9. Session Management
**Source:** `modules/ui_session.py`

**Description:** User session state persistence and restoration.

**Key Features:**
- Dark/light theme toggle with localStorage persistence
- Extension toggle without restart
- Settings export to `settings.yaml`
- Interface state persistence across reloads
- Boolean flags management

**Progenitor Application:**
- Persist user preferences
- Save agent state between sessions
- Quick configuration switching

**Story Points:** 3

---

### 10. Graceful Shutdown Handling
**Source:** `server.py`

**Description:** Clean shutdown with resource cleanup on interrupt signals.

**Key Features:**
- SIGINT handler for Ctrl+C
- Explicit model/server stop before exit
- Prevents `__del__` cleanup issues
- Logging of shutdown events

**Progenitor Application:**
- Ensure state is saved on shutdown
- Clean up background tasks
- Prevent data corruption

**Story Points:** 2

---

## 🟢 Priority 3: Nice-to-Have Features

### 11. Colored Logging
**Source:** `modules/logging_colors.py`

**Description:** Color-coded console output for different log levels.

**Key Features:**
- ANSI color codes for log levels
- Custom logger formatting
- Distinguishable output in terminal

**Progenitor Application:**
- Improve debugging experience
- Easier log scanning

**Story Points:** 1

---

### 12. Prompt Templates with Jinja2
**Source:** `modules/chat.py`

**Description:** Flexible prompt formatting using Jinja2 sandboxed templates.

**Key Features:**
- `ImmutableSandboxedEnvironment` for security
- `loopcontrols` extension support
- Custom global functions (`strftime_now`)
- Character name replacement
- System message injection
- Tool/function calling template support

**Progenitor Application:**
- Dynamic prompt construction
- Safe user-defined templates
- Multi-format output support

**Story Points:** 3

---

### 13. File Save/Delete UI
**Source:** `modules/ui_file_saving.py`

**Description:** In-UI file operations with confirmation dialogs.

**Key Features:**
- Preset saving/deleting
- Character saving/deleting
- Grammar file management
- Confirmation dialogs
- Root folder constraints

**Progenitor Application:**
- Safe file operations with user confirmation
- Artifact management within agent workspace

**Story Points:** 2

---

### 14. Multi-User Mode
**Source:** `modules/shared.py`

**Description:** Mode where chat histories are not saved/loaded per user.

**Key Features:**
- `--multi-user` flag
- Disabled file operations in multi-user mode
- Session isolation

**Progenitor Application:**
- Support multiple concurrent users
- Prevent cross-user data leakage

**Story Points:** 5

---

### 15. Idle Timeout Auto-Unload
**Source:** `modules/models.py`, `modules/shared.py`

**Description:** Automatically unload model after inactivity to free resources.

**Key Features:**
- `--idle-timeout` flag (minutes)
- Automatic reload on next request
- Last generation time tracking

**Progenitor Application:**
- Resource-efficient operation
- Support for limited GPU environments

**Story Points:** 3

---

### 16. Character/Persona System
**Source:** `modules/chat.py`, `docs/07 - Extensions.md`

**Description:** Custom AI characters with names, greetings, context, and profile pictures.

**Key Features:**
- Character YAML files
- Profile picture generation/caching
- Name substitution in prompts
- Character-specific contexts

**Progenitor Application:**
- Agent personas for different task types
- User-facing personality customization

**Story Points:** 5

---

### 17. Request Blocking
**Source:** `modules/block_requests.py`

**Description:** Block external HTTP requests during model loading.

**Key Features:**
- Context manager for blocking
- Monkey patching of request libraries
- Privacy protection during initialization

**Progenitor Application:**
- Prevent telemetry during sensitive operations
- Ensure offline capability

**Story Points:** 2

---

---

## Implementation Roadmap

### ✅ Sprint 0 (Completed 2026-01-29): User-Selected Features
- [x] Streaming Generation with Callbacks (5 pts) → `app/chat_server/streaming.py`
- [x] Conversation History with Metadata (8 pts) → `app/chat_server/conversation.py`
- [x] File Attachment System (5 pts) → `app/chat_server/attachments.py`
- [x] Session Management (3 pts) → `app/chat_server/session.py`
- [x] Graceful Shutdown Handling (2 pts) → `app/chat_server/shutdown.py`
- **Total: 23 points ✅**

### Sprint 1 (Week 1-2): Foundation
- [ ] Extension/Plugin System (13 pts)
- [x] ~~Graceful Shutdown Handling (2 pts)~~ - Completed in Sprint 0
- [ ] Colored Logging (1 pt)
- **Total: 14 points remaining**

### Sprint 2 (Week 3-4): Core Features
- [ ] OpenAI-Compatible API Layer (8 pts)
- [x] ~~Streaming Generation with Callbacks (5 pts)~~ - Completed in Sprint 0
- [ ] Preset/Configuration System (3 pts)
- **Total: 11 points remaining**

### Sprint 3 (Week 5-6): Context & Search
- [ ] Web Search Integration (5 pts)
- [x] ~~Conversation History with Metadata (8 pts)~~ - Completed in Sprint 0
- [x] ~~File Attachment System (5 pts)~~ - Completed in Sprint 0
- **Total: 5 points remaining**

### Sprint 4 (Week 7-8): Polish
- [x] ~~Session Management (3 pts)~~ - Completed in Sprint 0
- [ ] Prompt Templates with Jinja2 (3 pts)
- [ ] Idle Timeout Auto-Unload (3 pts)
- [ ] File Save/Delete UI (2 pts)
- **Total: 8 points remaining**

### Backlog (Future)
- [ ] Model Loader Abstraction (8 pts)
- [ ] Multi-User Mode (5 pts)
- [ ] Character/Persona System (5 pts)
- [ ] Request Blocking (2 pts)

---

## Technical Notes

### Dependencies to Consider
- `jinja2` - Template engine (sandboxed)
- `html2text` - HTML to Markdown conversion
- `pydub` - Audio processing (for TTS extensions)
- `gradio` - UI framework (consider alternatives for Progenitor)
- `pyyaml` - Configuration files

### Architecture Patterns from WebUI
1. **Shared State Module** (`shared.py`) - Global state container
2. **Loader Map Pattern** - Dictionary mapping to loader functions
3. **Iterator Transform** - Convert callbacks to generators
4. **Extension Hooks** - Named functions as extension points
5. **Metadata Dictionary** - Per-message key-value storage

---

## Acceptance Criteria Template

For each feature:
1. ✅ Unit tests with >80% coverage
2. ✅ Integration test with chat server
3. ✅ Documentation in `docs/`
4. ✅ Type hints for all public functions
5. ✅ Error handling with meaningful messages
6. ✅ Logging at appropriate levels
