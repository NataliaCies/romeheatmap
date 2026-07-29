"""Climate API endpoints — HTTP layer only."""
from datetime import date
from fastapi import APIRouter, Depends, Query
from app.models.schemas import (AlertSubscribeRequest, AlertSubscribeResponse,
    AlertCondition, CompareResponse, DistrictDetail, DistrictTimeseries, RomeOverview)
from app.services.climate_service import ClimateService
from app.utils.districts import DISTRICT_REGISTRY

router = APIRouter(prefix="/climate", tags=["climate"])

def get_climate_service() -> ClimateService: return ClimateService()

@router.get("/overview", response_model=RomeOverview)
async def get_overview(target_date: date = Query(default=None),
                       service: ClimateService = Depends(get_climate_service)) -> RomeOverview:
    """Full city heatmap — satellite data when available, weather fallback otherwise."""
    return await service.get_overview(target_date or date.today())

@router.get("/districts", response_model=list[dict])
async def list_districts() -> list[dict]:
    return [{"id": d.id, "label": d.label, "lat": d.lat, "lon": d.lon} for d in DISTRICT_REGISTRY.values()]

@router.get("/districts/{district_id}", response_model=DistrictDetail)
async def get_district(district_id: str, target_date: date = Query(default=None),
                       service: ClimateService = Depends(get_climate_service)) -> DistrictDetail:
    return await service.get_district(district_id, target_date or date.today())

@router.get("/compare", response_model=CompareResponse)
async def compare(a: str = Query(...), b: str = Query(...),
                  target_date: date = Query(default=None),
                  service: ClimateService = Depends(get_climate_service)) -> CompareResponse:
    return await service.compare(a, b, target_date or date.today())

@router.get("/timeseries/{district_id}", response_model=DistrictTimeseries)
async def get_timeseries(district_id: str, months: int = Query(default=12, ge=1, le=24),
                         service: ClimateService = Depends(get_climate_service)) -> DistrictTimeseries:
    return await service.get_timeseries(district_id, months)

@router.post("/alerts/subscribe", response_model=AlertSubscribeResponse)
async def subscribe_alert(body: AlertSubscribeRequest,
                          service: ClimateService = Depends(get_climate_service)) -> AlertSubscribeResponse:
    detail = await service.get_district(body.district_id, date.today())
    conditions = [
        AlertCondition(metric="LST °C", current_value=detail.mean_lst_celsius,
            threshold=body.threshold_lst_celsius, triggered=detail.mean_lst_celsius > body.threshold_lst_celsius),
        AlertCondition(metric="NDVI", current_value=detail.mean_ndvi,
            threshold=body.threshold_ndvi_min, triggered=detail.mean_ndvi < body.threshold_ndvi_min),
    ]
    triggered = [c for c in conditions if c.triggered]
    return AlertSubscribeResponse(subscribed=True, district_label=detail.label, email=body.email,
        current_lst_celsius=detail.mean_lst_celsius, current_ndvi=detail.mean_ndvi,
        current_livability=detail.livability_score, alerts_triggered=triggered,
        message=(f"Active alerts: {', '.join(c.metric for c in triggered)}" if triggered
                 else f"{detail.label} is within thresholds. Livability: {detail.livability_score}/100"))
