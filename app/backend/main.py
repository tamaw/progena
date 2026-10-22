"""FastAPI application assembly. ASGI entry point: app.backend.main:app."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

import app.backend.attachments.routes as attachments_routes
import app.backend.http.router as http_router
import app.backend.instructions.routes as instructions_routes
import app.backend.knowledge.routes as knowledge_routes
import app.backend.lifecycle.startup as lifecycle_startup
import app.backend.lifecycle.state as lifecycle_state
import app.backend.modules.routes as modules_routes
import app.backend.plugins.marketplace_routes as plugins_marketplace_routes
import app.backend.plugins.routes as plugins_routes
import app.backend.repositories.routes as repositories_routes
import app.backend.search.routes as search_routes
import app.backend.settings.preferences as settings_preferences
from app.backend.chat import responses
from app.backend.http import middleware

logger = logging.getLogger(__name__)


app = FastAPI(
    title=lifecycle_state.config.api_title,
    version=lifecycle_state.config.api_version,
    lifespan=lifecycle_startup.lifespan,
)
app.include_router(http_router.router)
app.include_router(search_routes.router)
app.include_router(knowledge_routes.router)
app.include_router(instructions_routes.router)
app.include_router(plugins_marketplace_routes.router)
app.include_router(repositories_routes.router)
app.include_router(plugins_routes.router)
app.include_router(settings_preferences.router)
app.include_router(modules_routes.router)
app.include_router(attachments_routes.router)


logger.info("Backend running in API-only mode (no static file serving)")


@app.get("/healthz", include_in_schema=False)
async def docker_healthz() -> Response:
    """Return 200 when the backend process is accepting HTTP requests."""
    return Response(status_code=200)


app.add_middleware(
    CORSMiddleware,
    allow_origins=lifecycle_state.config.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(
    GZipMiddleware,
    minimum_size=1024,
    compresslevel=6,
)
app.middleware("http")(middleware.request_tracking_middleware)
app.middleware("http")(middleware.request_id_middleware)
app.middleware("http")(middleware.rate_limit_middleware)
app.middleware("http")(middleware.request_metrics_middleware)
app.add_exception_handler(Exception, middleware.global_exception_handler)
app.include_router(responses.router)
