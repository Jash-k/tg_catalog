#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Preflight: wait until the database is reachable (up to ~60s).
#
# Railway boots the app before Postgres' private DNS/hostname is always
# ready, and a bad DATABASE_URL would otherwise crash-loop with an opaque
# `socket.gaierror`. This loop prints the (password-masked) target and waits.
# ---------------------------------------------------------------------------
echo "==> Preflight: resolving database target"
python - <<'PY'
import os
import re
import socket
import sys
import time
from urllib.parse import urlparse


def resolve_url() -> str:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url or "user:pass@host" in url:
        host = os.environ.get("PGHOST", "").strip()
        if host:
            user = os.environ.get("PGUSER", "postgres")
            password = os.environ.get("PGPASSWORD", "")
            port = os.environ.get("PGPORT", "5432")
            db = os.environ.get("PGDATABASE", "postgres")
            url = f"postgresql://{user}:{password}@{host}:{port}/{db}"
    return url


url = resolve_url()
if not url:
    print("[startup] ERROR: no DATABASE_URL (or PGHOST) is configured.", flush=True)
    print("[startup] Link the Railway Postgres plugin and set a reference", flush=True)
    print("[startup] variable: DATABASE_URL=${{Postgres.DATABASE_URL}}", flush=True)
    sys.exit(1)

normalized = re.sub(r"^postgres(?:ql)?\+\w+://", "postgresql://", url)
parsed = urlparse(normalized)
host, port = parsed.hostname or "", parsed.port or 5432
masked = re.sub(r"(://[^:/\s]+:)[^@\s]+(@)", r"\1***\2", url)
print(f"[startup] database target: {masked}", flush=True)
if host in ("host", "localhost", "127.0.0.1") and os.environ.get("RAILWAY_ENVIRONMENT"):
    print(f"[startup] WARNING: database host '{host}' looks wrong inside a", flush=True)
    print("[startup] Railway container (no local Postgres). Check DATABASE_URL.", flush=True)

attempts = 12
for attempt in range(1, attempts + 1):
    try:
        socket.getaddrinfo(host, port)
        with socket.create_connection((host, port), timeout=3):
            pass
        print(f"[startup] database reachable after {attempt} attempt(s)", flush=True)
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[startup] attempt {attempt}/{attempts}: database not reachable "
            f"({exc}); retrying in 5s",
            flush=True,
        )
        time.sleep(5)

print("[startup] ERROR: database never became reachable.", flush=True)
print("[startup] Verify DATABASE_URL / Postgres plugin in Railway, then redeploy.", flush=True)
sys.exit(1)
PY

echo "==> Running database migrations (alembic upgrade head)"
alembic upgrade head

echo "==> Starting uvicorn on port ${PORT:-8000}"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1
