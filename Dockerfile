# ---------------------------------------------------------------------------
# Tamil Content Catalog - production image (Railway.app single-service deploy)
# ---------------------------------------------------------------------------
FROM python:3.11-slim

# Python hygiene + no pip cache (smaller image)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies FIRST (layer caching: reinstalled only when
# requirements.txt changes, not on every code edit)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Non-root user for security
RUN useradd --create-home --shell /bin/bash appuser \
    && chmod +x /app/startup.sh \
    && chown -R appuser:appuser /app
USER appuser

# Railway injects PORT at runtime; 8000 is the local fallback.
EXPOSE 8000

# startup.sh: alembic upgrade head -> uvicorn on $PORT
CMD ["bash", "startup.sh"]
