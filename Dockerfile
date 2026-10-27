# Predictive Maintenance AI Agent — single image, two services (see
# docs/roadmap-next.md §4). Base: python:3.13-slim; non-root user; runtime
# data (SQLite + knowledge index) persists in the pma-data volume.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# dependency layer first (cached across code changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# application + knowledge corpus (runtime data lives in /app/data volume)
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY data/knowledge/ ./data/knowledge/
COPY docker/entrypoint_api.py ./docker/entrypoint_api.py

# non-root user; /app/data must be writable by the api service
RUN useradd --create-home appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
# api service health probe (slim image has no curl; streamlit service
# overrides/disables this in compose)
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PMA_API_PORT','8000')+'/health', timeout=4).status==200 else 1)"

ENTRYPOINT ["python", "docker/entrypoint_api.py"]
