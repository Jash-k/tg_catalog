#!/usr/bin/env bash
set -euo pipefail

echo "==> Running database migrations (alembic upgrade head)"
alembic upgrade head

echo "==> Starting uvicorn on port ${PORT:-8000}"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1
