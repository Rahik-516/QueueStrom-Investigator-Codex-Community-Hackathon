# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# QueueStorm Investigator - production-ready Dockerfile.
#
# Optimisations:
#   * python:3.11-slim base (~45 MB compressed)
#   * multi-stage build keeps build tooling out of the runtime image
#   * deps installed into a venv so we can copy the whole site-packages in
#     one layer and avoid shipping pip
#   * non-root user for security
#   * HEALTHCHECK against /health for orchestrators
# ---------------------------------------------------------------------------

# ---------- Stage 1: build --------------------------------------------------
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install build deps (only here, not in the runtime image)
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

# Create a venv we can copy wholesale into the runtime stage.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install -r requirements.txt


# ---------- Stage 2: runtime ------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    PORT=8000

# Create a non-root user first so the venv is owned by them too.
RUN groupadd --system app \
 && useradd  --system --gid app --home /app --shell /usr/sbin/nologin app

WORKDIR /app

# Copy the pre-built venv from the builder stage.
COPY --from=builder /opt/venv /opt/venv

# Copy the application code.
COPY main.py        ./main.py
COPY schemas.py     ./schemas.py
COPY llm_engine.py  ./llm_engine.py
COPY guardrails.py  ./guardrails.py

# Hand the directory to the unprivileged user.
RUN chown -R app:app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status==200 else 1)"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]