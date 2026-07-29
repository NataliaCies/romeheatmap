#!/usr/bin/env python3
"""Stage 2 smoke test — real Open-Meteo data, no Copernicus needed.

Run from project root:
    python scripts/test_weather.py
"""
import asyncio, sys
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent))
import app.services.weather_service as ws_mod
ws_mod.cache_get = AsyncMock(return_value=None)
ws_mod.cache_set = AsyncMock()

from app.services.weather_service import WeatherService


async def main():
    print(f"\n{'='*55}\n  Rome Climate API — Weather Smoke Test\n{'='*55}\n")
    svc = WeatherService()
    overview = await svc.get_overview(date.today())
    print(f"City: {overview.tmax_celsius}°C / {overview.tmin_celsius}°C  "
          f"UV={overview.uv_index}  sunrise={overview.sunrise}  sunset={overview.sunset}\n")
    print(f"  {'District':<22} {'LST':>7} {'NDVI':>6} {'Score':>6} {'Humidity':>9}")
    print(f"  {'-'*52}")
    for d in sorted(overview.districts, key=lambda x: -x.livability_score):
        print(f"  {d.label:<22} {d.mean_lst_celsius:>6.1f}°C "
              f"{d.mean_ndvi:>6.3f} {d.livability_score:>6.1f} {d.humidity_pct:>8.1f}%")
    print("\n✅ Done.")


if __name__ == "__main__":
    asyncio.run(main())
