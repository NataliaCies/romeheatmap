"""FastAPI application factory."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.router import api_router
from app.core.cache import close_redis
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.background_tasks import start_background_tasks, stop_background_tasks


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger = get_logger(__name__)
    settings = get_settings()
    logger.info("rome_climate_api_starting", env=settings.app_env)
    if settings.is_production:
        start_background_tasks()
    yield
    stop_background_tasks()
    await close_redis()
    logger.info("rome_climate_api_shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Rome Urban Climate API",
        description="Real-time urban heat island and vegetation monitor for Rome. "
                    "Powered by Sentinel-2 satellite imagery and Open-Meteo weather data.",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    app.add_middleware(CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"])
    register_exception_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
