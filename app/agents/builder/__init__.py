"""Builder agent package.

Importing this package is intentionally side-effect free. The runtime A2A
``app`` lives in :mod:`app.agents.builder.agent` and should be imported
explicitly by entrypoints (e.g. ``from app.agents.builder.agent import app``).
Helper modules such as :mod:`queue_worker` therefore do not pull in the
optional ``a2a`` runtime stack just because the package was imported.
"""

__all__: list[str] = []
