FROM python:3.11-slim-bookworm AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin libgdal-dev gcc g++ && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim-bookworm AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgdal32 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /install /usr/local
COPY app/ ./app/
RUN useradd -m -u 1001 appuser && chown -R appuser /app
USER appuser
RUN mkdir -p /tmp/rome_satellite_cache
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import httpx; r=httpx.get('http://localhost:8000/api/v1/health',timeout=8); exit(0 if r.status_code==200 else 1)"
CMD gunicorn app.main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers 2 --bind 0.0.0.0:${PORT:-8000} \
    --timeout 120 --keep-alive 5 \
    --log-level info --access-logfile - --error-logfile -
