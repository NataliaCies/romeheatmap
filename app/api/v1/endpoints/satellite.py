"""Satellite status and scene listing endpoints."""
from fastapi import APIRouter, Depends, Query
from app.services.copernicus_service import CopernicusService, CopernicusStatus, SceneSummary
from app.models.schemas import SatelliteStatusResponse, SceneListResponse, SceneSummarySchema

router = APIRouter(prefix="/satellite", tags=["satellite"])

def get_copernicus_service() -> CopernicusService: return CopernicusService()

@router.get("/status", response_model=SatelliteStatusResponse)
async def get_satellite_status(service: CopernicusService = Depends(get_copernicus_service)):
    """Check Copernicus integration status — credentials, connectivity, latest scene."""
    status = await service.get_status()
    return SatelliteStatusResponse(configured=status.configured, authenticated=status.authenticated,
        catalogue_reachable=status.catalogue_reachable,
        latest_scene_date=str(status.latest_scene_date) if status.latest_scene_date else None,
        latest_scene_cloud_pct=status.latest_scene_cloud_pct,
        scenes_last_30_days=status.scenes_last_30_days, error_message=status.error_message,
        stage=4, stage_description="Auth + scene search active. Raster download enabled.")

@router.get("/scenes", response_model=SceneListResponse)
async def list_scenes(days: int = Query(default=30, ge=1, le=90),
                      max_cloud_pct: float = Query(default=None, ge=0, le=100),
                      service: CopernicusService = Depends(get_copernicus_service)):
    """List available Sentinel-2 scenes over Rome."""
    scenes = await service.list_scenes(days=days, max_cloud_pct=max_cloud_pct)
    return SceneListResponse(total=len(scenes), days_searched=days, max_cloud_pct_filter=max_cloud_pct,
        scenes=[SceneSummarySchema(**s.__dict__) for s in scenes])
