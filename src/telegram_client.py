"""
Telethon MTProto client wrapper for lossless uncompressed document uploads.
"""

import asyncio
import logging
import random
from pathlib import Path
from typing import Optional, Dict, Any, Callable
from telethon import TelegramClient, errors
from telethon.network.connection import ConnectionTcpIntermediate
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
            connection=ConnectionTcpIntermediate,
        )
        self._target_entity = None

    async def connect(self) -> None:
        """Start the Telethon client and resolve target channel entity."""
        await asyncio.wait_for(self._do_connect(), timeout=35.0)

    async def _do_connect(self) -> None:
        if not self.client.is_connected():
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
                # Ensure client is connected before attempting send
                if not self.client.is_connected() or not self._target_entity:
                    logger.info("Telethon disconnected; reconnecting...")
                    try:
                        await self.connect()
                    except Exception as ce:
                        logger.warning(f"Reconnection failed ({ce}), re-creating TelethonClient...")
                        try:
                            await self.client.disconnect()
                        except Exception:
                            pass
                        from telethon.network import ConnectionTcpIntermediate
                        self.client = TelegramClient(
                            self.session,
                            config.TELEGRAM_API_ID,
                            config.TELEGRAM_API_HASH,
                            connection=ConnectionTcpIntermediate,
                        )
                        await self.connect()

                # Add organic jitter delay to prevent rate limit triggers
                jitter = random.uniform(0.1, 0.6)
                await asyncio.sleep(config.UPLOAD_DELAY_SECONDS + jitter)

                # Upload file: Use parallel MTProto chunk requests for all files with 128KB chunks
                upload_timeout = max(1200, int(file_size / (5 * 1024)))
                from src.parallel_uploader import upload_file_parallel
                file_handle = await asyncio.wait_for(
                    upload_file_parallel(
                        self.client,
                        file_path,
                        part_size_kb=128,
                        max_workers=4,
                        progress_callback=progress_callback,
                    ),
                    timeout=upload_timeout,
                )

                # Send pre-uploaded file handle strictly as document
                msg = await asyncio.wait_for(
                    self.client.send_file(
                        self._target_entity,
                        file=file_handle,
                        caption=caption,
                        parse_mode="html",
                        force_document=True,
                    ),
                    timeout=60,
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
            except (errors.RPCError, ConnectionError, TimeoutError, OSError, asyncio.TimeoutError) as e:
                retries += 1
                backoff = min(30, 2**retries)
                logger.warning(f"Transient error uploading {file_path.name}: {e}. Retrying in {backoff}s...")
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
                self._target_entity = None
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

