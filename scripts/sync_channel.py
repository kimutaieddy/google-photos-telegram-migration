"""
Catalog synchronizer: scans Telegram channel messages and populates local SQLite database.
Allows the web application to index and display all uploaded items directly from the channel!
"""

import re
import os
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from telethon.sync import TelegramClient
from src.config import config
from src.database import Database

def parse_caption(caption: str):
    """Extract metadata from standardized Telegram caption."""
    info = {"filename": None, "album": None, "sha256": None, "timestamp": None}
    if not caption:
        return info
    
    for line in caption.split("\n"):
        line = line.strip()
        if line.startswith("📁"):
            info["filename"] = line.replace("📁", "").strip()
        elif line.startswith("🏷️ Album:"):
            info["album"] = line.replace("🏷️ Album:", "").strip()
        elif line.startswith("🔒 SHA256:"):
            info["sha256"] = line.replace("🔒 SHA256:", "").strip()
        elif line.startswith("📅 Taken:"):
            info["timestamp"] = line.replace("📅 Taken:", "").strip()
    return info

def sync_channel(batch_size=50):
    db = Database()
    client = TelegramClient(
        str(config.DATA_DIR / "sync_indexer"),
        config.TELEGRAM_API_ID,
        config.TELEGRAM_API_HASH,
    )
    client.start(bot_token=config.BOT_TOKEN)

    print("🔎 Scanning channel for media...")
    low, high = 1, 20000
    last_id = 1
    while low <= high:
        mid = (low + high) // 2
        m = client.get_messages(config.TELEGRAM_CHANNEL_ID, ids=mid)
        if m:
            last_id = mid
            low = mid + 1
        else:
            high = mid - 1

    print(f"📡 Found channel latest message ID: {last_id}")

    arch_id = db.get_or_create_archive("telegram_cloud_channel")
    imported_count = 0

    # Fetch in batches
    for start_id in range(1, last_id + 1, batch_size):
        end_id = min(start_id + batch_size, last_id + 1)
        ids = list(range(start_id, end_id))
        msgs = client.get_messages(config.TELEGRAM_CHANNEL_ID, ids=ids)

        for m in msgs:
            if not m or not m.document:
                continue

            # Filename from document attributes or caption
            filename = getattr(m.file, "name", None)
            parsed = parse_caption(m.message or "")
            if not filename:
                filename = parsed["filename"] or f"media_{m.id}"

            size_bytes = m.document.size or 0
            mime_type = m.document.mime_type or "image/jpeg"
            sha256 = parsed["sha256"] or f"tg_doc_{m.document.id}"
            album = parsed["album"]
            exif_timestamp = parsed["timestamp"] or (m.date.isoformat() if m.date else None)

            # Register in database
            item_data = {
                "archive_id": arch_id,
                "relative_path": f"{album or 'Photos'}/{filename}",
                "filename": filename,
                "size_bytes": size_bytes,
                "sha256": sha256,
                "mime_type": mime_type,
                "exif_timestamp": exif_timestamp,
                "gps_lat": None,
                "gps_lon": None,
                "album": album,
                "description": None,
                "json_sidecar_path": None,
            }
            item_id = db.register_media_item(item_data)
            db.mark_item_uploaded(
                item_id=item_id,
                message_id=m.id,
                file_id=str(m.document.id),
                channel_id=str(config.TELEGRAM_CHANNEL_ID),
            )
            imported_count += 1

    client.disconnect()
    # Clean temporary session
    for f in os.listdir(config.DATA_DIR):
        if f.startswith("sync_indexer"):
            try:
                (config.DATA_DIR / f).unlink()
            except Exception:
                pass

    print(f"✅ Synchronized {imported_count} photos/videos into catalog!")

if __name__ == "__main__":
    sync_channel()
