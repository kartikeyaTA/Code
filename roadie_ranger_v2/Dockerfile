# ── Stage 1: build deps ───────────────────────────────────────────────────────
# Use slim to keep the final image small; install build tools only here.
FROM python:3.10-slim AS builder

WORKDIR /build

# System libs required to compile pyaudio (PortAudio) and other C extensions.
# These stay in the builder stage only.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libc6-dev \
        portaudio19-dev \
        libasound2-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install into an isolated prefix so we can copy cleanly to the runtime stage.
RUN pip install --upgrade pip \
 && pip install --prefix=/install --no-cache-dir -r requirements.txt azure-cli


# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.10-slim AS runtime

WORKDIR /app

# PortAudio shared library is needed at runtime by pyaudio.
# libasound2 is the ALSA backend used by PortAudio on Linux.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libportaudio2 \
        libasound2t64 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder.
COPY --from=builder /install /usr/local

# Copy application source.
COPY main.py acs_handler.py escalation_handler.py email_handler.py routing_config.py auth_layer.py storage_client.py ./
COPY static/ ./static/

# Create writable dirs for session JSON files and log files.
# Azure Container Apps runs as a non-root user by default, so we pre-create
# these and hand ownership to the app user (UID 1000).
RUN mkdir -p sessions logs \
 && useradd -u 1000 -m appuser \
 && chown -R appuser:appuser /app

USER appuser

# Azure Container Apps expects the app to listen on the port specified by the
# PORT env var (default 8080).  We honour it at runtime via the CMD below.
ENV PORT=8080

# Health-check so the container platform (and Azure LB) can verify readiness.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/')" \
    || exit 1

EXPOSE ${PORT}

# Use sh -c so $PORT is expanded at container start time, not image-build time.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1 --loop asyncio"]
