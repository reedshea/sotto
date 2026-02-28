FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for faster-whisper
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY sotto/ sotto/
COPY config.yaml.example .

RUN pip install --no-cache-dir .

# Default data directory
RUN mkdir -p /data/sotto

ENV SOTTO_CONFIG=/data/sotto/config.yaml

EXPOSE 8377

CMD ["sotto", "start", "--config", "/data/sotto/config.yaml"]
