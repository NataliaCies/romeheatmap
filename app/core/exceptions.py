"""Domain exceptions and centralised FastAPI exception handlers."""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.core.logging import get_logger

logger = get_logger(__name__)


class RomeClimateError(Exception): pass
class SatelliteDataError(RomeClimateError): pass
class WeatherDataError(RomeClimateError): pass
class CopernicusAuthError(RomeClimateError): pass
class NoSatelliteSceneError(RomeClimateError): pass
class GISProcessingError(RomeClimateError): pass
class DistrictNotFoundError(RomeClimateError): pass


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DistrictNotFoundError)
    async def district_not_found(request: Request, exc: DistrictNotFoundError):
        logger.warning("district_not_found", path=request.url.path, detail=str(exc))
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(NoSatelliteSceneError)
    async def no_scene(request: Request, exc: NoSatelliteSceneError):
        logger.warning("no_satellite_scene", detail=str(exc))
        return JSONResponse(status_code=503, content={"detail": str(exc), "hint": "Try a different date."})

    @app.exception_handler(CopernicusAuthError)
    async def copernicus_auth(request: Request, exc: CopernicusAuthError):
        logger.error("copernicus_auth_error", detail=str(exc))
        return JSONResponse(status_code=502, content={"detail": "Copernicus authentication failed. Check credentials."})

    @app.exception_handler(RomeClimateError)
    async def generic_domain(request: Request, exc: RomeClimateError):
        logger.error("domain_error", exc_type=type(exc).__name__, detail=str(exc))
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        logger.exception("unhandled_exception", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "An unexpected error occurred."})
