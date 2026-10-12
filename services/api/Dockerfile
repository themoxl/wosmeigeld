FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Build-Kontext ist das Repo-Root (siehe docker-compose.yml), damit
# services/worker/worker als Geschwisterverzeichnis von services/api
# eingebunden werden kann: app/services/import_service.py haengt
# services/worker anhand von Path(__file__).parents[3] relativ zu sich
# selbst an sys.path (siehe dort) und erwartet deshalb dieselbe
# Verzeichnistiefe wie im Repo (services/api/app/... neben services/worker).
WORKDIR /app/services/api

RUN groupadd --system app && useradd --system --gid app app

COPY services/api/requirements.txt ./
RUN pip install -r requirements.txt

COPY services/api/app ./app
COPY services/api/alembic.ini ./
COPY services/api/alembic ./alembic
COPY services/worker/worker ../worker/worker

RUN mkdir -p /health-documents && chown -R app:app /app /health-documents
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
