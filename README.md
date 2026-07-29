# Rome Urban Climate API

Backend for the **Rome Climate Monitor** — real-time urban heat island and vegetation index data for Rome.

- **Frontend**: https://vocal-marshmallow-7db69a.netlify.app
- **Data sources**: Sentinel-2 (NDVI + LST) via Copernicus + Open-Meteo (weather)
- **Deployment**: Railway (Docker) + Redis

## Quick start (local)

```bash
# 1. Install dependencies
pip install -r requirements-dev.txt

# 2. Copy and fill env file
cp .env.example .env

# 3. Start Redis
docker run -d -p 6379:6379 redis:7-alpine

# 4. Run API
uvicorn app.main:app --reload

# 5. Open docs
open http://localhost:8000/docs
```

## Smoke tests (no Copernicus needed)

```bash
python scripts/test_weather.py
```

## Post-deployment healthcheck

```bash
python scripts/healthcheck.py https://your-api.up.railway.app --full
```

## Deploy to Railway

See **DEPLOY_BEGINNER.md** for step-by-step guide (no IT knowledge needed).

## API endpoints

| Endpoint | Description |
|---|---|
| `GET /api/v1/health` | Liveness probe |
| `GET /api/v1/ready` | Readiness probe |
| `GET /api/v1/climate/overview` | Full city heatmap |
| `GET /api/v1/climate/districts` | List districts |
| `GET /api/v1/climate/districts/{id}` | District detail |
| `GET /api/v1/climate/compare?a=X&b=Y` | Compare districts |
| `GET /api/v1/climate/timeseries/{id}` | Monthly trend |
| `POST /api/v1/climate/alerts/subscribe` | Subscribe to alerts |
| `GET /api/v1/satellite/status` | Copernicus status |
| `GET /api/v1/satellite/scenes` | Available scenes |
| `GET /api/v1/cache/stats` | Cache statistics |
| `POST /api/v1/cache/warm` | Trigger cache warm |
| `DELETE /api/v1/cache/invalidate` | Invalidate cache |

## Architecture

```
app/
├── api/v1/endpoints/   HTTP only — thin layer
├── core/               config, logging, cache, exceptions, background tasks
├── models/             Pydantic schemas
├── repositories/       Open-Meteo, Copernicus auth+search+download
├── services/           ClimateService, WeatherService, CopernicusService
│   └── satellite/      LST pipeline, district stats
└── utils/              GIS math (numpy), district registry, timing
```
