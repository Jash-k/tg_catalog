"""Generate a Telethon SESSION_STRING for use on Railway.

Runs LOCALLY (not on Railway) because it needs interactive input:

    cp .env.example .env        # fill TELEGRAM_API_ID + TELEGRAM_API_HASH
    python scripts/generate_session.py

It asks for your phone number, completes the Telegram auth flow (code +
optional 2FA password), then prints the SESSION_STRING. Copy that string
into the TELEGRAM_SESSION_STRING variable on Railway.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow imports from the project root when run as `python scripts/...`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402


def main() -> None:
    load_dotenv()

    api_id_raw = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    if not api_id_raw or not api_hash:
        print(
            "ERROR: set TELEGRAM_API_ID and TELEGRAM_API_HASH first "
            "(create an app at https://my.telegram.org/apps)."
        )
        sys.exit(1)

    try:
        api_id = int(api_id_raw)
    except ValueError:
        print(f"ERROR: TELEGRAM_API_ID must be an integer, got {api_id_raw!r}")
        sys.exit(1)

    # Imported lazily so the file loads even without telethon installed globally.
    from telethon.sessions import StringSession
    from telethon.sync import TelegramClient

    print("Starting Telegram login. You will be asked for:")
    print("  1. Your phone number (international format, e.g. +919876543210)")
    print("  2. The login code Telegram sends you")
    print("  3. Your 2FA password, if enabled\n")

    with TelegramClient(StringSession(), api_id, api_hash) as client:
        session_string = client.session.save()

    print("\n" + "=" * 70)
    print("SUCCESS. Copy this SESSION_STRING into Railway env vars:")
    print("=" * 70)
    print()
    print(session_string)
    print()
    print("Set it as: TELEGRAM_SESSION_STRING=<the string above>")
    print("Keep it secret - it grants full access to your Telegram account.")


if __name__ == "__main__":
    main()
