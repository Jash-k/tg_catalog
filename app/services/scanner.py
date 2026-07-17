"""Telegram channel scanner: finds media filenames and feeds the pipeline.

Channels may be configured as usernames (``somechannel``), @handles,
``t.me/...`` links, or numeric marked channel IDs (``-100xxxxxxxxxx``).
Numeric IDs need the session account to have the channel in its dialogs,
so the entity cache is warmed on connect (``get_dialogs``).

Only metadata is ever persisted - NO file links, NO message IDs, NO file
hashes, NO quality info. The scanner is idempotent: a channel scanned twice
yields the same catalog state (tmdb_id existence check + seasons merge).
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional, Union

from telethon import TelegramClient
from telethon.errors import (
    ChannelInvalidError,
    ChannelPrivateError,
    FloodWaitError,
    UsernameNotOccupiedError,
)
from telethon.sessions import StringSession
from telethon.tl.types import (
    DocumentAttributeFilename,
    Message,
    PeerChannel,
    PeerChat,
)

from app.config import Settings, get_settings
from app.database import get_session_factory
from app.models.enums import ContentType
from app.services import catalog_service
from app.services.cleaner import CleaningPipeline
from app.services.matcher import TitleMatcher
from app.services.tmdb import TMDBService
from app.utils.helpers import (
    ARCHIVE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    VIDEO_MIME_TYPES,
    now_utc,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

_CHANNEL_MARK = 1_000_000_000_000  # bot-style mark in "-100<channel_id>" IDs
_NUMERIC_REF_RE = re.compile(r"^-?\d+$")
_DIALOG_CACHE_LIMIT = 500  # dialogs fetched to resolve numeric channel IDs


class TelegramScanner:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        tmdb_service: Optional[TMDBService] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client: Optional[TelegramClient] = None
        self.cleaner = CleaningPipeline()
        self.tmdb = tmdb_service or TMDBService(self.settings)
        self.matcher = TitleMatcher(self.tmdb, self.settings)
        self._connected = False
        self._scan_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    @property
    def is_configured(self) -> bool:
        return self.settings.telegram_configured

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> bool:
        if not self.is_configured:
            logger.warning(
                "Telegram not configured (set TELEGRAM_API_ID / TELEGRAM_API_HASH / "
                "TELEGRAM_SESSION_STRING); scanner disabled"
            )
            return False
        try:
            self.client = TelegramClient(
                StringSession(self.settings.TELEGRAM_SESSION_STRING),
                self.settings.telegram_api_id_int,
                self.settings.TELEGRAM_API_HASH,
            )
            await self.client.connect()
            if not await self.client.is_user_authorized():
                logger.error(
                    "Telegram session is not authorized - regenerate "
                    "TELEGRAM_SESSION_STRING with scripts/generate_session.py"
                )
                return False
            self._connected = True
            logger.info("Telegram client connected")
            await self._warm_entity_cache()
            return True
        except Exception as exc:  # noqa: BLE001 - never crash on startup
            logger.error("Telegram connect failed", extra={"error": str(exc)})
            return False

    async def disconnect(self) -> None:
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self._connected = False

    # ------------------------------------------------------------------
    # Channel reference resolution (usernames, links, -100... IDs)
    # ------------------------------------------------------------------

    async def _warm_entity_cache(self) -> None:
        """Load the account's dialogs so numeric channel IDs can resolve.

        With a StringSession, Telethon's in-memory access-hash cache starts
        empty; iterating the dialogs populates it, which is what makes
        ``get_entity(PeerChannel(...))`` work for joined channels.
        """
        try:
            count = 0
            async for _ in self.client.iter_dialogs(limit=_DIALOG_CACHE_LIMIT):
                count += 1
            logger.info("Telegram entity cache warmed", extra={"dialogs": count})
        except FloodWaitError as exc:
            logger.warning(
                "Flood wait while warming dialog cache", extra={"seconds": exc.seconds}
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not warm dialog cache", extra={"error": str(exc)})

    @staticmethod
    def resolve_channel_ref(ref: str) -> Union[int, str]:
        """Normalize a configured channel reference.

        '-1001234567890' / '1234567890' -> int (marked ID);
        'https://t.me/name', '@name', 'name' -> 'name' (username).
        """
        ref = ref.strip()
        ref = re.sub(
            r"^(?:https?://)?(?:www\.)?(?:t|telegram)\.me/", "", ref, flags=re.IGNORECASE
        )
        ref = ref.lstrip("@").strip().split("/")[0]
        if _NUMERIC_REF_RE.match(ref):
            return int(ref)
        return ref

    async def _get_channel_entity(self, channel_ref: str):
        """Resolve a configured channel reference to a Telethon entity.

        Handles bot-style marked IDs: ``-100<channel_id>`` -> PeerChannel and
        ``-<chat_id>`` -> PeerChat as fallbacks when direct lookup fails.
        """
        resolved = self.resolve_channel_ref(channel_ref)
        try:
            return await self.client.get_entity(resolved)
        except (ValueError, ChannelInvalidError):
            if isinstance(resolved, int):
                if resolved < -_CHANNEL_MARK:
                    channel_id = (-resolved) - _CHANNEL_MARK
                    return await self.client.get_entity(PeerChannel(channel_id))
                if resolved < 0:
                    return await self.client.get_entity(PeerChat(-resolved))
                if resolved > _CHANNEL_MARK:
                    return await self.client.get_entity(
                        PeerChannel(resolved - _CHANNEL_MARK)
                    )
            raise

    # ------------------------------------------------------------------
    # Filename extraction
    # ------------------------------------------------------------------

    @staticmethod
    def extract_video_filename(message: Message) -> Optional[str]:
        """Return the video filename for media messages, else None.

        Skips images, audio-only files, text messages, stickers, polls, and
        ZIP/archive files.
        """
        filename: Optional[str] = None

        document = getattr(message, "document", None)
        if document is not None:
            mime = (getattr(document, "mime_type", "") or "").lower()
            for attribute in document.attributes:
                if isinstance(attribute, DocumentAttributeFilename):
                    filename = attribute.file_name
                    break
            if not filename and getattr(message, "file", None) is not None:
                filename = getattr(message.file, "name", None)

            if filename:
                lowered = filename.lower()
                if lowered.endswith(ARCHIVE_EXTENSIONS):
                    return None
                if mime in VIDEO_MIME_TYPES or lowered.endswith(VIDEO_EXTENSIONS):
                    return filename
                return None  # document but not video (image/pdf/audio/archive)

            # Document with no filename: fall back to caption text.
            caption = (message.message or "").strip()
            return caption[:300] if caption else None

        # Plain video media (not sent as document).
        if getattr(message, "video", None) is not None:
            if getattr(message, "file", None) is not None:
                filename = getattr(message.file, "name", None)
            if filename:
                lowered = filename.lower()
                if lowered.endswith(ARCHIVE_EXTENSIONS):
                    return None
                return filename
            caption = (message.message or "").strip()
            return caption[:300] if caption else None

        return None

    # ------------------------------------------------------------------
    # Per-filename processing
    # ------------------------------------------------------------------

    async def process_filename(
        self, session, filename: str, channel_username: str
    ) -> str:
        """Run one filename through the pipeline. Returns a report key:
        'matched' | 'unmatched' | 'skipped' | 'updated'."""
        cleaned = self.cleaner.clean(filename)
        if cleaned.parse_failed:
            await catalog_service.record_unmatched(
                session,
                original_filename=filename,
                cleaned=cleaned,
                channel_username=channel_username,
                reason="parse_failed",
            )
            return "unmatched"

        assert cleaned.cleaned_title is not None

        # Already in the catalog? Skip the TMDB call but still merge seasons.
        existing = await catalog_service.find_by_title_year(
            session, cleaned.cleaned_title, cleaned.detected_year
        )
        if existing is not None:
            if (
                cleaned.content_type == ContentType.SERIES.value
                and cleaned.season_number is not None
            ):
                if catalog_service.merge_available_seasons(existing, cleaned.season_number):
                    session.add(existing)
                    return "updated"
            return "skipped"

        outcome = await self.matcher.match(cleaned)
        if not outcome.matched:
            await catalog_service.record_unmatched(
                session,
                original_filename=filename,
                cleaned=cleaned,
                channel_username=channel_username,
                reason=outcome.reason or "no_tmdb_match",
            )
            return "unmatched"

        item, created = await catalog_service.upsert_item(session, outcome.data or {})
        logger.info(
            "Catalog item upserted",
            extra={
                "tmdb_id": item.tmdb_id,
                "catalog": item.catalog_type,
                "title": item.title_english,
                "created": created,
                "score": outcome.score,
            },
        )
        return "matched" if created else "updated"

    # ------------------------------------------------------------------
    # Channel scanning
    # ------------------------------------------------------------------

    async def scan_channel(
        self, channel_username: str, incremental: bool = True
    ) -> Dict[str, Any]:
        report: Dict[str, Any] = {
            "channel": channel_username,
            "incremental": incremental,
            "scanned": 0,
            "matched": 0,
            "unmatched": 0,
            "skipped": 0,
            "updated": 0,
        }

        if not self._connected or self.client is None:
            logger.warning("Scanner not connected; skipping channel scan",
                           extra={"channel": channel_username})
            report["error"] = "not_connected"
            return report

        # One channel scan at a time across the whole process.
        if self._scan_lock.locked():
            logger.info("Another scan already in progress; skipping",
                        extra={"channel": channel_username})
            report["error"] = "scan_in_progress"
            return report

        async with self._scan_lock:
            try:
                entity = await self._get_channel_entity(channel_username)
            except (UsernameNotOccupiedError, ValueError) as exc:
                logger.error(
                    "Channel not found / not resolvable (for -100 IDs the "
                    "session account must be a member of the channel)",
                    extra={"channel": channel_username, "ref": str(exc)[:200]},
                )
                report["error"] = "channel_not_found"
                return report
            except (ChannelPrivateError, ChannelInvalidError):
                logger.error("Channel is private / not joined",
                             extra={"channel": channel_username})
                report["error"] = "channel_private"
                return report
            except FloodWaitError as exc:
                logger.warning("Flood wait while resolving channel",
                               extra={"seconds": exc.seconds})
                report["error"] = "flood_wait"
                await asyncio.sleep(exc.seconds)
                return report

            factory = get_session_factory()
            async with factory() as session:
                tracker = await catalog_service.get_or_create_tracker(
                    session, channel_username
                )
                last_known = tracker.last_message_id or 0
                min_id = last_known if (incremental and last_known > 0) else 0
                highest_id = min_id
                batch_count = 0

                logger.info(
                    "Channel scan start",
                    extra={
                        "channel": channel_username,
                        "incremental": incremental,
                        "min_id": min_id,
                    },
                )

                try:
                    async for message in self.client.iter_messages(
                        entity, min_id=min_id, reverse=True
                    ):
                        if message.id:
                            highest_id = max(highest_id, message.id)

                        filename = self.extract_video_filename(message)
                        if not filename:
                            continue

                        report["scanned"] += 1
                        try:
                            result_key = await self.process_filename(
                                session, filename, channel_username
                            )
                            report[result_key] = report.get(result_key, 0) + 1
                        except Exception as exc:  # noqa: BLE001 - never crash on one bad file
                            logger.exception(
                                "Failed to process filename",
                                extra={"filename": filename[:200], "error": str(exc)},
                            )
                            report["unmatched"] += 1

                        batch_count += 1
                        if batch_count >= int(self.settings.BATCH_SIZE):
                            tracker.last_message_id = highest_id
                            tracker.total_scanned = (tracker.total_scanned or 0) + report["scanned"]
                            tracker.total_matched = (tracker.total_matched or 0) + (
                                report["matched"] + report["updated"]
                            )
                            tracker.total_unmatched = (tracker.total_unmatched or 0) + report["unmatched"]
                            await session.commit()
                            report["scanned"] = report["matched"] = report["updated"] = report["unmatched"] = 0
                            batch_count = 0
                            await asyncio.sleep(1)  # be kind to Telegram flood limits

                except FloodWaitError as exc:
                    logger.warning(
                        "FloodWaitError during scan; pausing then aborting this run",
                        extra={"seconds": exc.seconds, "channel": channel_username},
                    )
                    await asyncio.sleep(exc.seconds)
                    report["error"] = "flood_wait"

                tracker.last_message_id = max(tracker.last_message_id or 0, highest_id)
                tracker.total_scanned = (tracker.total_scanned or 0) + report["scanned"]
                tracker.total_matched = (tracker.total_matched or 0) + (
                    report["matched"] + report["updated"]
                )
                tracker.total_unmatched = (tracker.total_unmatched or 0) + report["unmatched"]
                tracker.last_scanned_at = now_utc()
                await session.commit()

        logger.info("Channel scan complete", extra=report)
        return report

    async def scan_all_channels(self, incremental: bool = True) -> List[Dict[str, Any]]:
        reports: List[Dict[str, Any]] = []
        for channel in self.settings.telegram_channels_list:
            try:
                reports.append(await self.scan_channel(channel, incremental=incremental))
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Channel scan crashed",
                    extra={"channel": channel, "error": str(exc)},
                )
                reports.append({"channel": channel, "error": str(exc)})
        return reports
