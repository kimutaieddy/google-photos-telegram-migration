"""
Telethon MTProto client wrapper for lossless uncompressed document uploads.
"""

import asyncio
import logging
import random
from pathlib import Path
from typing import Optional, Dict, Any, Callable
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from src.config import config
from src.metadata import MediaMetadata

logger = logging.getLogger(__name__)


class TelegramArchiver:
    """Handles lossless document uploads and downloads via Telethon MTProto."""

    def __init__(self):
        config.validate_telegram_config()
        self.session = (
            StringSession(config.TELEGRAM_SESSION_STRING)
            if config.TELEGRAM_SESSION_STRING
            else str(config.DATA_DIR / config.TELEGRAM_SESSION_NAME)
        )
        self.client = TelegramClient(
            self.session,
            config.TELEGRAM_API_ID,
            config.TELEGRAM_API_HASH,
        )
        self._target_entity = None

    async def connect(self) -> None:
        """Start the Telethon client and resolve target channel entity."""
        await self.client.connect()
        if not await self.client.is_user_authorized():
            if config.BOT_TOKEN:
                logger.info("Authorizing Telethon client via BOT_TOKEN...")
                await self.client.start(bot_token=config.BOT_TOKEN)
            else:
                raise PermissionError(
                    "Telegram client is not authorized. Please provide BOT_TOKEN or run 'python scripts/generate_session.py'."
                )
        # Resolve target channel / chat
        self._target_entity = await self.client.get_input_entity(config.TELEGRAM_CHANNEL_ID)
        me = await self.client.get_me()
        logger.info(f"Connected to Telegram as {me.first_name} (ID: {me.id})")

    async def disconnect(self) -> None:
        """Disconnect the Telethon client."""
        if self.client.is_connected():
            await self.client.disconnect()

    async def send_status_message(self, text: str) -> Optional[int]:
        """Send an administrative or progress notification to the target channel."""
        if not self._target_entity:
            await self.connect()
        try:
            msg = await self.client.send_message(self._target_entity, text, parse_mode="html")
            return msg.id
        except Exception as e:
            logger.error(f"Failed to send status message: {e}")
            return None

    async def upload_document(
        self,
        file_path: Path,
        metadata: MediaMetadata,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[str, Any]:
        """
        Upload media file as an uncompressed document to preserve exact binary hash and EXIF.
        Handles FLOOD_WAIT and transient network retries.
        """
        if not self._target_entity:
            await self.connect()

        file_size = file_path.stat().st_size
        if file_size > config.MAX_FILE_SIZE_BYTES:
            raise ValueError(
                f"File {file_path.name} exceeds max Telegram size ({file_size} > {config.MAX_FILE_SIZE_BYTES} bytes)"
            )

        caption = metadata.format_telegram_caption()
        retries = 0

        while retries < config.MAX_RETRIES:
            try:
                # Add organic jitter delay to prevent rate limit triggers
                jitter = random.uniform(0.1, 0.6)
                await asyncio.sleep(config.UPLOAD_DELAY_SECONDS + jitter)

                # Send strictly as document (force_document=True)
                # to prevent Telegram from transcoding or stripping metadata
                msg = await self.client.send_file(
                    self._target_entity,
                    file=str(file_path),
                    caption=caption,
                    parse_mode="html",
                    force_document=True,
                    progress_callback=progress_callback,
                )

                file_id = None
                if msg.document:
                    file_id = str(msg.document.id)

                return {
                    "message_id": msg.id,
                    "file_id": file_id,
                    "channel_id": str(config.TELEGRAM_CHANNEL_ID),
                    "size_bytes": file_size,
                }

            except errors.FloodWaitError as e:
                logger.warning(f"Telegram FloodWait encountered: sleeping for {e.seconds + 2} seconds...")
                await asyncio.sleep(e.seconds + 2)
                retries += 1
            except (errors.RPCError, ConnectionError, TimeoutError) as e:
                retries += 1
                backoff = min(60, 2**retries)
                logger.warning(f"Transient error uploading {file_path.name}: {e}. Retrying in {backoff}s...")
                await asyncio.sleep(backoff)

        raise RuntimeError(f"Failed to upload {file_path.name} after {config.MAX_RETRIES} retries")

    async def download_document(
        self,
        message_id: int,
        destination_path: Path,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Path:
        """Download a document message back from Telegram for verification."""
        if not self._target_entity:
            await self.connect()

        destination_path.parent.mkdir(parents=True, exist_ok=True)
        msg = await self.client.get_messages(self._target_entity, ids=message_id)
        if not msg or not msg.document:
            raise ValueError(f"Message ID {message_id} does not contain a valid document")

        await self.client.download_media(
            msg,
            file=str(destination_path),
            progress_callback=progress_callback,
        )
        return destination_path

