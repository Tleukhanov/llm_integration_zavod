FROM python:3.11-slim

# System deps: ffmpeg + timezone data + tini (PID 1 signal/pid reaping)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates tzdata tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Web console port (uvicorn in docker-compose binds 0.0.0.0:8000)
EXPOSE 8000

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project source
COPY . .

# Do NOT set SHORTS_* env vars here; they come from .env at runtime.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Entrypoint — tini as PID 1 forwards SIGTERM/SIGINT to python so a
# `docker stop` cleanly tears down child ffmpeg processes. No args:
# channel defaults from SHORTS_CHANNEL (env / .env), clip count via
# --env-file at runtime (e.g. docker run --env-file .env ...).
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "scripts/multi_channel.py"]
