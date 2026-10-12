FROM python:3.12-slim AS base

# PYTHONPATH: der Worker importiert den KI-Kern und weitere Motoren aus
# services/api (`app.*`, z. B. app/llm, app/services/contract_matching_service)
# - auf Modulebene, also bevor ein Modul den Pfad selbst setzen koennte. Die
# frueheren sys.path-Kniffe in den Worker-Modulen sind dadurch ersetzt.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/services/api

# Build-Kontext ist das Repo-Root (siehe docker-compose.yml), spiegelbildlich zu
# services/api/Dockerfile: services/api/app wird als Geschwisterverzeichnis
# mitkopiert (/app/services/api/app), dieselbe Verzeichnistiefe wie im Repo.
WORKDIR /app/services/worker

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-deu \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system app && useradd --system --gid app app

COPY services/worker/requirements.txt ./
RUN pip install -r requirements.txt

COPY services/worker/worker ./worker
COPY services/api/app ../api/app

RUN mkdir -p /health-documents && chown -R app:app /app /health-documents
USER app

CMD ["dramatiq", "worker.actors", "--processes", "1", "--threads", "4"]
