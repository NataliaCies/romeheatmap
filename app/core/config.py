"""Application configuration loaded from environment variables."""
from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)
    app_env: str = "development"
    log_level: str = "INFO"
    allowed_origins: list[str] = ["https://vocal-marshmallow-7db69a.netlify.app", "http://localhost:3000"]
    copernicus_client_id: str = ""
    copernicus_client_secret: str = ""
    copernicus_token_url: str = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
    copernicus_search_url: str = "https://catalogue.dataspace.copernicus.eu/odata/v1"
    copernicus_download_url: str = "https://zipper.dataspace.copernicus.eu/api/v1/dataspace/products"
    open_meteo_base_url: str = "https://api.open-meteo.com/v1"
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_weather: int = 1800
    cache_ttl_satellite: int = 43200
    cache_ttl_districts: int = 3600
    cache_ttl_overview: int = 900
    tile_size_px: int = 512
    max_cloud_cover_pct: int = 30
    rome_bbox_lon_min: float = 12.35
    rome_bbox_lat_min: float = 41.78
    rome_bbox_lon_max: float = 12.62
    rome_bbox_lat_max: float = 41.98

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, v):
        return [o.strip() for o in v.split(",")] if isinstance(v, str) else v

    @property
    def is_production(self) -> bool: return self.app_env == "production"

    @property
    def rome_bbox(self) -> tuple[float, float, float, float]:
        return (self.rome_bbox_lon_min, self.rome_bbox_lat_min, self.rome_bbox_lon_max, self.rome_bbox_lat_max)


@lru_cache(maxsize=1)
def get_settings() -> Settings: return Settings()
