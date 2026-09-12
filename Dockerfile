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

# Entrypoint — override via docker run or compose if needed
CMD ["python", "scripts/multi_channel.py"]
