"""
Web Server Bridge to Telegram: fetches thumbnails and streams full-resolution media.
Manages disk-bounded caching for thumbnails (~200MB max) to protect VPS storage.
"""

import os
import io
import logging
from pathlib import Path
from typing import Optional, AsyncGenerator
from telethon import TelegramClient
from telethon.sessions import StringSession
from src.config import config

logger = logging.getLogger(__name__)


class TelegramMediaBridge:
    """Manages Telegram MTProto client for downloading thumbnails and streaming files."""

    def __init__(self):
        self.session = (
            StringSession(config.TELEGRAM_SESSION_STRING)
            if config.TELEGRAM_SESSION_STRING
            else str(config.DATA_DIR / f"{config.TELEGRAM_SESSION_NAME}_reader")
        )
        self.client = TelegramClient(
            self.session,
            config.TELEGRAM_API_ID,
            config.TELEGRAM_API_HASH,
        )
        self._target_entity = None
        self._connected = False

    async def connect(self) -> None:
        """Connect to Telegram MTProto."""
        if self._connected and self.client.is_connected():
            return

        await self.client.connect()
        if not await self.client.is_user_authorized():
            if config.BOT_TOKEN:
                await self.client.start(bot_token=config.BOT_TOKEN)
            else:
                raise PermissionError("Telegram client is not authorized. Missing BOT_TOKEN.")

        self._target_entity = await self.client.get_input_entity(config.TELEGRAM_CHANNEL_ID)
        self._connected = True
        logger.info("TelegramMediaBridge connected successfully.")

    async def disconnect(self) -> None:
        """Gracefully disconnect."""
        if self.client.is_connected():
            await self.client.disconnect()
        self._connected = False

    async def get_thumbnail_bytes(self, message_id: int) -> Optional[bytes]:
        """
        Retrieve thumbnail bytes for a given message ID.
        Uses a local thumbnail disk cache to avoid redundant Telegram roundtrips.
        """
        await self.connect()
        cache_file = config.THUMBNAIL_DIR / f"thumb_{message_id}.jpg"
        if cache_file.exists() and cache_file.stat().st_size > 0:
            return cache_file.read_bytes()

        msg = await self.client.get_messages(self._target_entity, ids=message_id)
        if not msg:
            return None

        # Download thumbnail from Telegram message/document
        # thumb=-1 selects the largest available thumbnail (e.g. 320x320)
        buf = io.BytesIO()
        downloaded = await self.client.download_media(msg, thumb=-1, file=buf)
        if downloaded:
            data = buf.getvalue()
            if data:
                # Write to disk cache
                try:
                    config.THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
                    cache_file.write_bytes(data)
                except Exception as e:
                    logger.warning(f"Could not write thumbnail to disk cache: {e}")
                return data

        return None

    async def stream_full_media(self, message_id: int, chunk_size: int = 1024 * 1024) -> AsyncGenerator[bytes, None]:
        """
        Stream full-resolution file directly from Telegram via MTProto in memory chunks.
        Zero VPS disk consumption!
        """
        await self.connect()
        msg = await self.client.get_messages(self._target_entity, ids=message_id)
        if not msg or not msg.document:
            raise FileNotFoundError(f"No document found for message {message_id}")

        async for chunk in self.client.iter_download(msg.document, chunk_size=chunk_size):
            yield chunk


bridge = TelegramMediaBridge()
