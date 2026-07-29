"""Pydantic models for all API request/response payloads."""
from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, Field, EmailStr, field_validator


class HeatPoint(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    lst_celsius: float
    ndvi: float = Field(..., ge=-1, le=1)
    intensity: float = Field(..., ge=0, le=1)
    cloud_masked: bool = False

class DistrictSummary(BaseModel):
    id: str; label: str; lat: float; lon: float
    mean_lst_celsius: float; mean_ndvi: float; humidity_pct: float
    livability_score: float = Field(..., ge=0, le=100)
    cloud_masked_pct: float = Field(..., ge=0, le=100)
    data_source: Literal["sentinel2","estimated","fallback","sentinel2+open-meteo","open-meteo+uhi-model"]
    scene_date: date | None = None

class DistrictDetail(DistrictSummary):
    heat_points: list[HeatPoint]; green_points: list[HeatPoint]
    tmax_celsius: float | None = None; tmin_celsius: float | None = None

class RomeOverview(BaseModel):
    date: date; tmax_celsius: float; tmin_celsius: float
    humidity_pct: float; pressure_hpa: float; uv_index: float
    cloud_pct: float; wind_kmh: float; sunrise: str; sunset: str
    districts: list[DistrictSummary]
    heat_points: list[HeatPoint]; green_points: list[HeatPoint]
    data_source: str

class TimeseriesPoint(BaseModel):
    date: date; month_label: str; mean_lst_celsius: float
    mean_ndvi: float; humidity_pct: float; livability_score: float
    cloud_masked_pct: float

class DistrictTimeseries(BaseModel):
    district_id: str; district_label: str; points: list[TimeseriesPoint]

class DistrictCompareItem(BaseModel):
    id: str; label: str; mean_lst_celsius: float
    mean_ndvi: float; humidity_pct: float; livability_score: float

class CompareResponse(BaseModel):
    date: date
    district_a: DistrictCompareItem; district_b: DistrictCompareItem
    delta_lst_celsius: float; delta_ndvi: float
    delta_livability: float; delta_humidity_pct: float

class AlertSubscribeRequest(BaseModel):
    email: EmailStr; district_id: str
    threshold_lst_celsius: float = Field(38.0, ge=20, le=55)
    threshold_ndvi_min: float = Field(0.1, ge=0, le=1)

    @field_validator("district_id")
    @classmethod
    def district_must_be_known(cls, v: str) -> str:
        from app.utils.districts import DISTRICT_REGISTRY
        if v not in DISTRICT_REGISTRY:
            raise ValueError(f"Unknown district '{v}'")
        return v

class AlertCondition(BaseModel):
    metric: str; current_value: float; threshold: float; triggered: bool

class AlertSubscribeResponse(BaseModel):
    subscribed: bool; district_label: str; email: str
    current_lst_celsius: float; current_ndvi: float; current_livability: float
    alerts_triggered: list[AlertCondition]; message: str

class HealthResponse(BaseModel):
    status: Literal["ok","degraded"]; version: str
    timestamp: datetime; services: dict[str, Literal["ok","error"]]

class SceneSummarySchema(BaseModel):
    scene_id: str; product_name: str; sensing_date: str
    cloud_cover_pct: float = Field(..., ge=0, le=100)
    orbit_direction: str; online: bool
    size_mb: float = Field(..., ge=0); quicklook_url: str
    days_ago: int = Field(..., ge=0)

class SatelliteStatusResponse(BaseModel):
    configured: bool; authenticated: bool; catalogue_reachable: bool
    latest_scene_date: str | None; latest_scene_cloud_pct: float | None
    scenes_last_30_days: int; error_message: str | None
    stage: int; stage_description: str

class SceneListResponse(BaseModel):
    total: int; days_searched: int; max_cloud_pct_filter: float | None
    scenes: list[SceneSummarySchema]
    model_config = {"arbitrary_types_allowed": True}
