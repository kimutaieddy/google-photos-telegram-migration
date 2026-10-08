"""
End-to-end migration pipeline with bounded memory and disk usage.
"""

import logging
import os
import shutil
import zipfile
from pathlib import Path
from typing import List, Optional
from src.config import config
from src.database import Database
from src.metadata import extract_full_metadata, compute_file_sha256
from src.telegram_client import TelegramArchiver
from src.downloader import download_file_resumable, assert_disk_space_available

logger = logging.getLogger(__name__)

# Extensions recognized as media assets
MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif",
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".3gp",
    ".raw", ".dng", ".cr2", ".nef", ".arw",
}


class MigrationPipeline:
    """Orchestrates the sequential processing of Takeout archives to Telegram."""

    def __init__(self, db: Optional[Database] = None, archiver: Optional[TelegramArchiver] = None):
        config.ensure_directories()
        self.db = db or Database()
        self.archiver = archiver or TelegramArchiver()
        self.staging_dir = config.TEMP_DIR / "staging"
        self.staging_dir.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        """Connect to Telegram and verify environment."""
        await self.archiver.connect()

    async def close(self) -> None:
        """Clean up connections."""
        await self.archiver.disconnect()

    def is_media_file(self, filename: str) -> bool:
        """Determine if a file is an image or video."""
        ext = Path(filename).suffix.lower()
        return ext in MEDIA_EXTENSIONS and not Path(filename).name.startswith("._")

    async def process_zip_archive(self, zip_path: Path, archive_id: int, delete_zip_after: bool = False) -> None:
        """
        Process a single 2 GB Takeout zip file sequentially:
        Extracts one file at a time, uploads to Telegram, and immediately deletes temporary data.
        """
        logger.info(f"Opening archive: {zip_path.name}")
        self.db.update_archive_status(archive_id, "PROCESSING")

        with zipfile.ZipFile(zip_path, "r") as z:
            all_names = z.namelist()
            media_files = [f for f in all_names if self.is_media_file(f)]
            logger.info(f"Found {len(media_files)} media items inside {zip_path.name}")
            self.db.update_archive_status(archive_id, "PROCESSING", total_files=len(media_files))

            processed_count = 0
            for rel_path_str in media_files:
                rel_path = Path(rel_path_str)
                filename = rel_path.name

                # Check if already processed
                existing = self.db.get_pending_items(archive_id=archive_id)
                # Check directly in DB if already uploaded
                with self.db.get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT id, status FROM media_items WHERE archive_id = ? AND relative_path = ?",
                        (archive_id, rel_path_str),
                    )
                    row = cur.fetchone()
                    if row and row["status"] in ("UPLOADED", "VERIFIED"):
                        logger.info(f"Skipping already uploaded: {filename}")
                        processed_count += 1
                        continue

                # Ensure minimal disk headroom before extracting single file
                assert_disk_space_available(self.staging_dir, required_mb=100)

                # Extract only this media file and possible sidecar JSON
                temp_media_path = self.staging_dir / filename
                with open(temp_media_path, "wb") as f_out:
                    f_out.write(z.read(rel_path_str))

                # Check and extract sidecar JSON if present in archive
                json_candidates = [
                    f"{rel_path_str}.json",
                    rel_path_str.rsplit(".", 1)[0] + ".json",
                ]
                extracted_json_path = None
                for j_cand in json_candidates:
                    if j_cand in all_names:
                        temp_json = self.staging_dir / Path(j_cand).name
                        with open(temp_json, "wb") as f_out:
                            f_out.write(z.read(j_cand))
                        extracted_json_path = temp_json
                        break

                try:
                    # Extract metadata and calculate SHA-256
                    meta = extract_full_metadata(temp_media_path, rel_path)

                    # Deduplication check: check if exact file was already uploaded in another archive
                    dup = self.db.find_item_by_sha256(meta.sha256)
                    if dup:
                        logger.info(f"Identical duplicate detected for {filename} (Matches msg {dup['telegram_message_id']}).")

                    # Register in SQLite catalog
                    item_data = {
                        "archive_id": archive_id,
                        "relative_path": rel_path_str,
                        "filename": meta.filename,
                        "size_bytes": meta.size_bytes,
                        "sha256": meta.sha256,
                        "mime_type": meta.mime_type,
                        "exif_timestamp": meta.exif_timestamp,
                        "gps_lat": meta.gps_lat,
                        "gps_lon": meta.gps_lon,
                        "album": meta.album,
                        "description": meta.description,
                        "json_sidecar_path": meta.json_sidecar_path,
                    }
                    item_id = self.db.register_media_item(item_data)

                    # Upload as uncompressed lossless document to Telegram
                    upload_res = await self.archiver.upload_document(temp_media_path, meta)

                    # Update database on successful upload
                    self.db.mark_item_uploaded(
                        item_id=item_id,
                        message_id=upload_res["message_id"],
                        file_id=upload_res.get("file_id"),
                        channel_id=upload_res["channel_id"],
                    )
                    processed_count += 1
                    self.db.update_archive_status(archive_id, "PROCESSING", processed_files=processed_count)
                    logger.info(f"Successfully archived [{processed_count}/{len(media_files)}]: {filename}")

                except Exception as e:
                    logger.error(f"Error processing {filename}: {e}", exc_info=True)
                    if "item_id" in locals():
                        self.db.mark_item_failed(item_id, str(e))
                finally:
                    # Immediate disk cleanup of the extracted temporary files
                    if temp_media_path.exists():
                        temp_media_path.unlink()
                    if extracted_json_path and extracted_json_path.exists():
                        extracted_json_path.unlink()

        # Archive fully processed
        self.db.update_archive_status(archive_id, "COMPLETED", processed_files=processed_count)
        logger.info(f"Completed processing archive: {zip_path.name}")

        # Delete the zip archive if requested to free VPS disk
        if delete_zip_after and zip_path.exists():
            zip_path.unlink()
            logger.info(f"Deleted local archive {zip_path.name} to free disk space.")

    async def run_from_directory(self, archives_dir: Path, delete_after_upload: bool = False) -> None:
        """Process all Takeout ZIP files present in a directory."""
        await self.initialize()
        try:
            zip_files = sorted(list(archives_dir.glob("*.zip")))
            if not zip_files:
                logger.warning(f"No ZIP archives found in {archives_dir}")
                return

            for zip_file in zip_files:
                arch_id = self.db.get_or_create_archive(
                    archive_name=zip_file.name,
                    size_bytes=zip_file.stat().st_size,
                )
                arch_info = self.db.get_archive(arch_id)
                if arch_info and arch_info["status"] == "COMPLETED":
                    logger.info(f"Archive {zip_file.name} is already completed. Skipping.")
                    continue

                await self.process_zip_archive(zip_file, arch_id, delete_zip_after=delete_after_upload)

        finally:
            await self.close()

    async def run_from_urls(self, urls: List[str]) -> None:
        """
        Download and process Takeout chunks sequentially from signed URLs.
        Downloads one 2 GB chunk, uploads all its files to Telegram, deletes the chunk,
        and proceeds to the next.
        """
        await self.initialize()
        try:
            for idx, url in enumerate(urls, 1):
                archive_name = f"takeout-chunk-{idx:03d}.zip"
                arch_id = self.db.get_or_create_archive(archive_name=archive_name, source_url=url)
                arch_info = self.db.get_archive(arch_id)
                if arch_info and arch_info["status"] == "COMPLETED":
                    logger.info(f"Chunk {archive_name} already completed. Skipping.")
                    continue

                dest_zip = config.TEMP_DIR / archive_name
                logger.info(f"Downloading chunk {idx}/{len(urls)}: {archive_name}")
                download_file_resumable(url, dest_zip)

                await self.process_zip_archive(dest_zip, arch_id, delete_zip_after=True)

        finally:
            await self.close()

