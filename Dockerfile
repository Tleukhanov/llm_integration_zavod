FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/user

# Install system packages (including ffmpeg and fonts for captions)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg git ca-certificates fonts-liberation fontconfig \
    && rm -rf /var/lib/apt/lists/*

# Set up non-root user for Hugging Face Spaces compliance
RUN useradd -m -u 1000 user
WORKDIR /app

# Prepare directories and permissions
RUN mkdir -p /app/outputs && chown -R user:user /app

USER user
ENV PATH="/home/user/.local/bin:${PATH}"

COPY --chown=user:user pyproject.toml README.md LICENSE ./
COPY --chown=user:user shorts_clipper ./shorts_clipper

RUN python -m pip install --upgrade pip \
    && pip install -e . \
    && pip install -U --no-cache-dir git+https://github.com/yt-dlp/yt-dlp.git

EXPOSE 7860
CMD ["uvicorn", "shorts_clipper.api.server:app", "--host", "0.0.0.0", "--port", "7860", "--proxy-headers", "--forwarded-allow-ips=*"]

