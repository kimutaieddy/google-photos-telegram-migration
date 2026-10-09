"""
End-to-end migration pipeline with bounded memory and disk usage.
"""

import asyncio
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
from src.gdrive import GoogleDriveManager

logger = logging.getLogger(__name__)

# Extensions recognized as media assets
MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif",
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".3gp",
    ".raw", ".dng", ".cr2", ".nef", ".arw",
}


class MigrationPipeline:
    """Orchestrates the sequential processing of Takeout archives to Telegram."""

    def __init__(
        self,
        db: Optional[Database] = None,
        archiver: Optional[TelegramArchiver] = None,
        workers: int = 2,
        max_gb: Optional[float] = None,
    ):
        config.ensure_directories()
        self.db = db or Database()
        self.archiver = archiver or TelegramArchiver()
        self.staging_dir = config.TEMP_DIR / "staging"
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.workers = max(1, workers)
        self.max_bytes_limit = int(max_gb * 1024 * 1024 * 1024) if max_gb else None
        self.total_uploaded_bytes_session = 0
        self.db_lock = asyncio.Lock()
        self.stop_requested = False

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
        Process a single 2 GB Takeout zip file:
        Extracts files with bounded staging, uploads via concurrent workers to Telegram,
        and immediately deletes temporary extracted files.
        """
        logger.info(f"Opening archive: {zip_path.name}")
        self.db.update_archive_status(archive_id, "PROCESSING")

        with zipfile.ZipFile(zip_path, "r") as z:
            all_names = set(z.namelist())
            # Sort media items by uncompressed size ascending: photos and smaller media first, large videos last
            infolist_dict = {info.filename: info.file_size for info in z.infolist()}
            media_files = sorted(
                [f for f in all_names if self.is_media_file(f)],
                key=lambda f: infolist_dict.get(f, 0),
            )
            logger.info(
                f"Found {len(media_files)} media items inside {zip_path.name} "
                f"(prioritizing smaller files first, running {self.workers} worker(s))"
            )
            self.db.update_archive_status(archive_id, "PROCESSING", total_files=len(media_files))

            processed_count = 0
            queue = asyncio.Queue()
            for rel_str in media_files:
                queue.put_nowait(rel_str)

            zip_read_lock = asyncio.Lock()

            async def _worker(worker_id: int):
                nonlocal processed_count
                while not queue.empty() and not self.stop_requested:
                    try:
                        rel_path_str = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                    rel_path = Path(rel_path_str)
                    filename = rel_path.name

                    # 1. Check in DB if already uploaded for this archive
                    async with self.db_lock:
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
                                queue.task_done()
                                continue

                    # 2. Check minimal disk headroom before extracting
                    assert_disk_space_available(self.staging_dir, required_mb=100)

                    # 3. Read media and sidecar bytes safely from zip
                    temp_media_path = self.staging_dir / f"w{worker_id}_{filename}"
                    json_candidates = [
                        f"{rel_path_str}.json",
                        rel_path_str.rsplit(".", 1)[0] + ".json",
                    ]
                    json_cand_found = None
                    for j_cand in json_candidates:
                        if j_cand in all_names:
                            json_cand_found = j_cand
                            break

                    async with zip_read_lock:
                        media_bytes = z.read(rel_path_str)
                        json_bytes = z.read(json_cand_found) if json_cand_found else None

                    with open(temp_media_path, "wb") as f_out:
                        f_out.write(media_bytes)

                    extracted_json_path = None
                    if json_bytes:
                        extracted_json_path = self.staging_dir / f"w{worker_id}_{Path(json_cand_found).name}"
                        with open(extracted_json_path, "wb") as f_out:
                            f_out.write(json_bytes)

                    item_id = None
                    try:
                        # Extract metadata and calculate SHA-256
                        meta = extract_full_metadata(temp_media_path, rel_path)

                        # Deduplication: check if identical binary hash already uploaded
                        existing = None
                        async with self.db_lock:
                            existing = self.db.find_item_by_sha256(meta.sha256)

                        if existing and existing.get("telegram_message_id"):
                            logger.info(f"File {filename} already uploaded (msg {existing['telegram_message_id']}). Reusing metadata.")
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
                            async with self.db_lock:
                                item_id = self.db.register_media_item(item_data)
                                self.db.mark_item_uploaded(
                                    item_id=item_id,
                                    message_id=existing["telegram_message_id"],
                                    file_id=existing.get("telegram_file_id"),
                                    channel_id=str(existing.get("telegram_channel_id", config.TELEGRAM_CHANNEL_ID)),
                                )
                                processed_count += 1
                                self.db.update_archive_status(archive_id, "PROCESSING", processed_files=processed_count)
                            continue

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
                        async with self.db_lock:
                            item_id = self.db.register_media_item(item_data)

                        # Upload to Telegram as uncompressed lossless document
                        upload_res = await self.archiver.upload_document(temp_media_path, meta)

                        async with self.db_lock:
                            self.db.mark_item_uploaded(
                                item_id=item_id,
                                message_id=upload_res["message_id"],
                                file_id=upload_res.get("file_id"),
                                channel_id=upload_res["channel_id"],
                            )
                            processed_count += 1
                            self.total_uploaded_bytes_session += meta.size_bytes
                            self.db.update_archive_status(archive_id, "PROCESSING", processed_files=processed_count)

                        logger.info(
                            f"Successfully archived [{processed_count}/{len(media_files)}]: {filename} "
                            f"({meta.size_bytes / (1024*1024):.2f} MB)"
                        )

                        # Check if session max GB limit reached
                        if self.max_bytes_limit and self.total_uploaded_bytes_session >= self.max_bytes_limit:
                            logger.info(
                                f"🛑 Reached session upload limit of {self.max_bytes_limit / (1024**3):.2f} GB "
                                f"({self.total_uploaded_bytes_session / (1024**3):.2f} GB uploaded). Signaling stop."
                            )
                            self.stop_requested = True

                    except Exception as e:
                        logger.error(f"Error processing {filename}: {e}", exc_info=True)
                        if item_id:
                            async with self.db_lock:
                                self.db.mark_item_failed(item_id, str(e))
                    finally:
                        if temp_media_path.exists():
                            try:
                                temp_media_path.unlink()
                            except Exception as ue:
                                logger.warning(f"Could not immediately delete {temp_media_path.name}: {ue}")
                        if extracted_json_path and extracted_json_path.exists():
                            try:
                                extracted_json_path.unlink()
                            except Exception:
                                pass
                        queue.task_done()

            worker_tasks = [asyncio.create_task(_worker(i)) for i in range(self.workers)]
            await asyncio.gather(*worker_tasks)

        # Update archive completion status if not stopped early
        if not self.stop_requested:
            self.db.update_archive_status(archive_id, "COMPLETED", processed_files=processed_count)
            logger.info(f"Completed processing archive: {zip_path.name}")
        else:
            logger.info(
                f"Archive {zip_path.name} partially processed ({processed_count}/{len(media_files)}) "
                f"due to session limit."
            )

        # Delete the zip archive if requested to free VPS disk
        if delete_zip_after and zip_path.exists():
            try:
                zip_path.unlink()
                logger.info(f"Deleted local archive {zip_path.name} to free disk space.")
            except Exception as ze:
                logger.warning(f"Could not immediately delete {zip_path.name}: {ze}")

    async def run_from_directory(self, archives_dir: Path, delete_after_upload: bool = False) -> None:
        """Process all Takeout ZIP files present in a directory."""
        await self.initialize()
        try:
            zip_files = sorted(list(archives_dir.glob("*.zip")))
            if not zip_files:
                logger.warning(f"No ZIP archives found in {archives_dir}")
                return

            for zip_file in zip_files:
                if self.stop_requested:
                    break
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
                if self.stop_requested:
                    break
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

    async def run_from_gdrive(
        self,
        folder_name: str = "Takeout",
        prefix: Optional[str] = None,
        reverse: bool = False,
    ) -> None:
        """
        Automatically discover and process Takeout archives directly from Google Drive.
        Downloads each 2 GB ZIP sequentially, uploads to Telegram, and removes it locally.
        If reverse=True, processes archives in reverse order (e.g. for concurrent VPS execution).
        """
        drive_mgr = GoogleDriveManager()
        drive_mgr.authenticate()
        archives = drive_mgr.list_takeout_archives(folder_name=folder_name, prefix=prefix)

        if not archives:
            logger.warning(f"No archives found in Google Drive folder: '{folder_name}'")
            return

        if reverse:
            logger.info("Reverse order enabled: processing archives from the end (descending order).")
            archives = list(reversed(archives))

        await self.initialize()
        try:
            for idx, arch in enumerate(archives, 1):
                if self.stop_requested:
                    logger.info("Session limit reached. Halting Google Drive archive processing.")
                    break

                arch_id = self.db.get_or_create_archive(
                    archive_name=arch["name"],
                    size_bytes=arch["size_bytes"],
                )
                arch_info = self.db.get_archive(arch_id)
                if arch_info and arch_info["status"] == "COMPLETED":
                    logger.info(f"Archive {arch['name']} is already completed. Skipping.")
                    continue

                dest_zip = config.TEMP_DIR / arch["name"]
                size_mb = arch["size_bytes"] / (1024 * 1024)
                logger.info(
                    f"Downloading Drive archive [{idx}/{len(archives)}]: {arch['name']} ({size_mb:.1f} MB)..."
                )

                drive_mgr.download_archive(
                    file_id=arch["id"],
                    destination_path=dest_zip,
                    expected_size_mb=int(size_mb) + 100,
                )

                await self.process_zip_archive(dest_zip, arch_id, delete_zip_after=True)

                if self.stop_requested:
                    logger.info(
                        f"Session upload limit reached ({self.total_uploaded_bytes_session / (1024**3):.2f} GB). "
                        "Halting pipeline."
                    )
                    break

        finally:
            await self.close()


