# syntax=docker/dockerfile:1
FROM python:3.11-slim

# --- Metadata ---
LABEL org.opencontainers.image.title="VSN300 → PVOutput Bridge" \
    org.opencontainers.image.description="Bridge between ABB VSN300 inverter and PVOutput.org with Modbus decoding and web dashboard." \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.authors="David Deeds" \
    org.opencontainers.image.url="https://hub.docker.com/r/daviddeeds/vsn300-pvoutput"

# --- Environment settings ---
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Australia/Perth

# --- Install system deps ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates tzdata netcat-openbsd curl \
    && rm -rf /var/lib/apt/lists/*

# --- Set working directory ---
WORKDIR /app

# --- Copy and install dependencies first (for layer caching) ---
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# --- Copy app source ---
COPY web_dashboard.py .

# --- License and attribution files ---
COPY LICENSE NOTICE LICENSES.txt README.md /app/

# --- Copy favicon ---
COPY static/ /app/static/

# --- Create persistent data volume ---
VOLUME ["/data"]

# --- Network / health / runtime setup ---
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=5 \
    CMD nc -z localhost 8080 || exit 1

# --- Run ---
CMD ["python", "-u", "web_dashboard.py"]