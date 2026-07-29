# Deployment Guide — Rome Climate API

Krok po kroku od lokalnego kodu do działającego backendu połączonego z frontendem.

---

## Wymagania przed startem

- Konto GitHub
- Konto Railway (railway.app) — darmowy tier wystarczy
- Konto Copernicus Data Space (dataspace.copernicus.eu) — bezpłatne
- Frontend już działa: https://vocal-marshmallow-7db69a.netlify.app

---

## Krok 1 — Utwórz repozytorium GitHub

```bash
cd rome-climate-backend
git init
git add .
git commit -m "Initial commit — Rome Climate API"
```

Wejdź na **github.com → New repository** → nazwa: `rome-climate-backend` → Create.

```bash
git remote add origin https://github.com/TWOJ_USERNAME/rome-climate-backend
git push -u origin main
```

---

## Krok 2 — Zarejestruj się na Copernicus Data Space

1. Wejdź na **https://dataspace.copernicus.eu**
2. Kliknij **Register** → wypełnij formularz → potwierdź email
3. Zaloguj się → kliknij swój profil (prawy górny róg) → **Settings**
4. Przejdź do **OAuth Clients** → kliknij **+ Add Client**
5. Nazwa: `rome-climate-api`, typ: `Confidential`
6. Zapisz — dostaniesz `CLIENT_ID` i `CLIENT_SECRET`
   ⚠️ `CLIENT_SECRET` widzisz **tylko raz** — zapisz go od razu!

---

## Krok 3 — Utwórz projekt na Railway

1. Wejdź na **railway.app** → **New Project**
2. Wybierz **Deploy from GitHub repo**
3. Wybierz repozytorium `rome-climate-backend`
4. Railway automatycznie wykryje `Dockerfile` i rozpocznie build

> ⏱ Pierwszy build trwa ~5–8 minut (kompilacja GDAL/rasterio)

---

## Krok 4 — Dodaj Redis na Railway

1. W dashboardzie projektu → kliknij **+ Add Service**
2. Wybierz **Redis** (oficjalny plugin Railway)
3. Railway automatycznie ustawi zmienną `REDIS_URL` w projekcie
4. Nie musisz nic konfigurować — działa out of the box

---

## Krok 5 — Ustaw zmienne środowiskowe

W Railway → Twoja usługa API → zakładka **Variables** → dodaj:

| Zmienna | Wartość |
|---|---|
| `APP_ENV` | `production` |
| `LOG_LEVEL` | `INFO` |
| `COPERNICUS_CLIENT_ID` | *(twój Client ID z kroku 2)* |
| `COPERNICUS_CLIENT_SECRET` | *(twój Client Secret z kroku 2)* |
| `ALLOWED_ORIGINS` | `https://vocal-marshmallow-7db69a.netlify.app` |
| `MAX_CLOUD_COVER_PCT` | `30` |
| `CACHE_TTL_WEATHER` | `1800` |
| `CACHE_TTL_SATELLITE` | `43200` |
| `CACHE_TTL_DISTRICTS` | `3600` |
| `TILE_SIZE_PX` | `512` |
| `ROME_BBOX_LON_MIN` | `12.35` |
| `ROME_BBOX_LAT_MIN` | `41.78` |
| `ROME_BBOX_LON_MAX` | `12.62` |
| `ROME_BBOX_LAT_MAX` | `41.98` |

> `REDIS_URL` Railway ustawia automatycznie z kroku 4.

Po zapisaniu Railway automatycznie redeploy'uje aplikację.

---

## Krok 6 — Sprawdź deployment

Railway da ci URL w formie `https://rome-climate-backend-production-xxxx.up.railway.app`.

```bash
# Podstawowy healthcheck
curl https://TWOJ_URL.up.railway.app/api/v1/health

# Pełny post-deployment test
python scripts/healthcheck.py https://TWOJ_URL.up.railway.app --full
```

Oczekiwany output:
```
✅ API reachable           status=ok
✅ Redis connected         redis=ok
✅ Districts list          10 districts
✅ Overview endpoint       source=open-meteo+uhi-model tmax=34.0°C
✅ District detail         LST=39.2°C score=31.5
✅ Compare endpoint        Δ LST centro-villa=+11.7°C
✅ Cache stats             hits=3 misses=5 rate=37.5%
✅ Satellite status        ✓ configured, 6 scenes last 30 days
```

---

## Krok 7 — Podłącz frontend do backendu

Otwórz plik `rome-climate-map.html` i znajdź linię:

```javascript
const API_BASE = '';  // empty = API not deployed yet → use local data
```

Zmień na:

```javascript
const API_BASE = 'https://TWOJ_URL.up.railway.app/api/v1';
```

Zapisz plik i wgraj na Netlify:
1. Wejdź na **app.netlify.com** → Twoja strona
2. Przeciągnij zaktualizowany `rome-climate-map.html` na stronę deploymentu
3. Lub użyj Netlify CLI: `netlify deploy --prod`

---

## Krok 8 — Weryfikacja end-to-end

Otwórz https://vocal-marshmallow-7db69a.netlify.app

W prawym dolnym rogu karty legendy zobaczysz:
- 🟢 `Sentinel-2 NDVI + LST (real satellite data)` — gdy są dane satelitarne
- 🟡 `Open-Meteo + UHI model (satellite unavailable)` — gdy fallback

Sprawdź w DevTools (F12 → Network):
- Request do `TWOJ_URL.up.railway.app/api/v1/climate/overview` → 200 OK
- `data_source` w odpowiedzi

---

## Architektura po deploymencie

```
[Przeglądarka / Netlify]
        │
        │ fetch API_BASE + /climate/overview
        ▼
[Railway — FastAPI + Gunicorn]
        │
        ├── Redis (Railway addon) ← cache 30min–12h
        │
        ├── Open-Meteo API ──────── zawsze dostępne, bez klucza
        │
        └── Copernicus Data Space ─ Sentinel-2 scenes
                                    (wymaga CLIENT_ID + SECRET)
```

---

## Środowisko produkcyjne vs deweloperskie

| | Development | Production (Railway) |
|---|---|---|
| `APP_ENV` | `development` | `production` |
| Background tasks | Wyłączone | Włączone (warming o północy) |
| Workers | 1 (uvicorn reload) | 2 (gunicorn + uvicorn) |
| Redis | localhost:6379 | Railway addon |
| Logs | Console kolorowy | JSON strukturalny |
| CORS | `*` | tylko Netlify URL |

---

## Aktualizacje po deploymencie

Każdy push do `main` na GitHub automatycznie triggeruje redeploy na Railway:

```bash
git add .
git commit -m "fix: improve LST calibration"
git push origin main
# Railway automatycznie redeploy'uje w ~2-3 minuty
```

---

## Monitoring i logi

W Railway → Twoja usługa → zakładka **Logs**:

Szukaj w logach:
- `startup_warming_begin` — aplikacja startuje
- `copernicus_token_obtained` — auth działa
- `satellite_scene_selected` — znaleziono scenę
- `satellite_fallback_triggered` — fallback aktywny (normalny gdy brak sceny)
- `cache_warm_done` — cache warming zakończony

---

## Troubleshooting

**Build fails — GDAL not found**
```
# Upewnij się że Dockerfile używa gdal-bin + libgdal-dev w stage builder
# i libgdal32 w stage runtime
```

**Redis connection refused**
```
# Sprawdź czy Redis addon jest dodany w Railway
# REDIS_URL powinien być automatycznie ustawiony
```

**Copernicus 401 Unauthorized**
```
# Sprawdź CLIENT_ID i CLIENT_SECRET w zmiennych Railway
# Token URL musi być:
# https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token
```

**Overview zwraca tylko open-meteo (bez satelity)**
```
# To jest NORMALNE gdy:
# - Brak sceny z <30% chmur w oknie 10 dni
# - Copernicus nie skonfigurowany
# Sprawdź /api/v1/satellite/status dla szczegółów
```

**Frontend nie łączy się z API (CORS error)**
```
# Sprawdź ALLOWED_ORIGINS w Railway — musi zawierać dokładny URL Netlify
# Bez trailing slash: https://vocal-marshmallow-7db69a.netlify.app
```
