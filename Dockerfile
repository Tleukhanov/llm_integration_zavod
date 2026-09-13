FROM python:3.11-slim

# System deps: ffmpeg + timezone data
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project source
COPY . .

# Do NOT set SHORTS_* env vars here; they come from .env at runtime.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Entrypoint — no args: channel defaults from SHORTS_CHANNEL (env / .env),
# clip count via --env-file at runtime (e.g. docker run --env-file .env ...).
CMD ["python", "scripts/multi_channel.py"]
