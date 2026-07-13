"""Agent registry routes for backend-managed deploy integration."""

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agents"])
