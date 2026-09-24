FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY pyproject.toml alembic.ini config.yaml ./
COPY app ./app
RUN pip install --no-cache-dir .

# One image for the collector and the API; docker-compose overrides the command (and the
# healthcheck) per service. Default: the collector.
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-m", "app.collector.healthcheck"]

CMD ["python", "-m", "app.collector"]
