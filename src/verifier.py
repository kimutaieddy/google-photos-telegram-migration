"""
Verification engine: validates byte-for-byte integrity against Telegram copies.
"""

import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from src.config import config
from src.database import Database
from src.metadata import compute_file_sha256
from src.telegram_client import TelegramArchiver

logger = logging.getLogger(__name__)


class IntegrityVerifier:
    """Performs cryptographic byte-for-byte verification of uploaded Telegram documents."""

    def __init__(self, db: Optional[Database] = None, archiver: Optional[TelegramArchiver] = None):
        self.db = db or Database()
        self.archiver = archiver or TelegramArchiver()
        self.verify_temp_dir = config.TEMP_DIR / "verification"
        self.verify_temp_dir.mkdir(parents=True, exist_ok=True)

    async def verify_item(self, item_id: int) -> Dict[str, Any]:
        """
        Download a file back from Telegram, compute SHA-256, and compare with source hash.
        """
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM media_items WHERE id = ?", (item_id,))
            row = cur.fetchone()

        if not row:
            raise ValueError(f"Item ID {item_id} not found in database")

        item = dict(row)
        msg_id = item.get("telegram_message_id")
        expected_sha256 = item.get("sha256")
        expected_size = item.get("size_bytes")
        filename = item.get("filename")

        if not msg_id:
            raise ValueError(f"Item {filename} has not been uploaded yet (no telegram_message_id)")

        temp_target = self.verify_temp_dir / f"verify_{item_id}_{filename}"

        try:
            # Download file directly from Telegram MTProto
            await self.archiver.download_document(message_id=msg_id, destination_path=temp_target)

            actual_size = temp_target.stat().st_size
            actual_sha256 = compute_file_sha256(temp_target)

            size_match = actual_size == expected_size
            hash_match = actual_sha256 == expected_sha256

            if hash_match and size_match:
                self.db.mark_item_verified(item_id)
                logger.info(f"VERIFIED: {filename} (SHA-256: {actual_sha256}) byte-for-byte identical.")
                return {
                    "item_id": item_id,
                    "filename": filename,
                    "success": True,
                    "source_sha256": expected_sha256,
                    "telegram_sha256": actual_sha256,
                    "size_bytes": actual_size,
                    "status": "VERIFIED",
                }
            else:
                err_msg = (
                    f"Integrity mismatch for {filename}! "
                    f"Expected SHA: {expected_sha256}, Actual: {actual_sha256}. "
                    f"Expected Size: {expected_size}, Actual: {actual_size}"
                )
                logger.error(err_msg)
                with self.db.get_connection() as conn:
                    conn.execute(
                        "UPDATE media_items SET status = 'VERIFICATION_FAILED', error_message = ? WHERE id = ?",
                        (err_msg, item_id),
                    )
                return {
                    "item_id": item_id,
                    "filename": filename,
                    "success": False,
                    "source_sha256": expected_sha256,
                    "telegram_sha256": actual_sha256,
                    "status": "VERIFICATION_FAILED",
                }

        finally:
            if temp_target.exists():
                temp_target.unlink()

    async def verify_sample(self, sample_size: int = 10) -> List[Dict[str, Any]]:
        """Verify a random or sequential sample of uploaded items."""
        await self.archiver.connect()
        try:
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT id FROM media_items
                    WHERE status = 'UPLOADED'
                    ORDER BY RANDOM() LIMIT ?
                    """,
                    (sample_size,),
                )
                rows = cur.fetchall()

            results = []
            for r in rows:
                res = await self.verify_item(r["id"])
                results.append(res)
            return results
        finally:
            await self.archiver.disconnect()

