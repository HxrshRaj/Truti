FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
COPY truti_ingest ./truti_ingest
RUN pip install --no-cache-dir .
ENV PORT=8000
# api: default. worker: `python -m truti_ingest.worker`
CMD ["sh", "-c", "uvicorn truti_ingest.main:app_factory --factory --host 0.0.0.0 --port ${PORT} --workers ${WEB_CONCURRENCY:-2}"]
