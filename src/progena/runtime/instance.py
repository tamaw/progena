"""Process-wide identity for a running Progena runtime."""

from __future__ import annotations

import uuid

# Deliberately allocated once per interpreter process, rather than per graph or
# controller rebuild, so durable lifecycle records identify their true owner.
RUNTIME_INSTANCE_ID = uuid.uuid4().hex
