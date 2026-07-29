"""API v1 router."""
from fastapi import APIRouter
from app.api.v1.endpoints import cache, climate, health, satellite

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(climate.router)
api_router.include_router(satellite.router)
api_router.include_router(cache.router)
