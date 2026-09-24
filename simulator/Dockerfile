FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY pyproject.toml ./
COPY cell_sim ./cell_sim
RUN pip install --no-cache-dir .

COPY config.yaml ./

# Retentive data (simulated clock, counters, electrode wear): mount a volume here.
VOLUME /app/state

EXPOSE 4840
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-m", "cell_sim.healthcheck"]

CMD ["python", "-m", "cell_sim"]
