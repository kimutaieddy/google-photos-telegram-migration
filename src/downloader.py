"""
Safe, resumable downloader with disk headroom enforcement.
"""

import logging
import shutil
from pathlib import Path
from typing import Optional, Callable
import requests
from src.config import config

logger = logging.getLogger(__name__)


class DiskSpaceError(RuntimeError):
    """Raised when available disk space is insufficient."""
    pass


def get_free_disk_mb(target_dir: Path) -> float:
    """Return free disk space in megabytes for the given directory."""
    target_dir.mkdir(parents=True, exist_ok=True)
    total, used, free = shutil.disk_usage(target_dir)
    return free / (1024 * 1024)


def assert_disk_space_available(target_dir: Path, required_mb: int = 2500) -> None:
    """Check if the disk has sufficient space beyond the minimum threshold."""
    free_mb = get_free_disk_mb(target_dir)
    if free_mb < (required_mb + config.MIN_DISK_FREE_MB):
        raise DiskSpaceError(
            f"Insufficient disk space: {free_mb:.1f} MB free, need at least {required_mb + config.MIN_DISK_FREE_MB} MB "
            f"(including safety buffer {config.MIN_DISK_FREE_MB} MB)."
        )


def download_file_resumable(
    url: str,
    destination_path: Path,
    expected_size_mb: Optional[int] = None,
    headers: Optional[dict] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Path:
    """
    Download a file from an HTTP/HTTPS URL with resume support (HTTP Range)
    and proactive disk headroom verification.
    """
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    req_mb = expected_size_mb or 2200
    assert_disk_space_available(destination_path.parent, required_mb=req_mb)

    download_headers = headers.copy() if headers else {}
    existing_bytes = 0

    if destination_path.exists():
        existing_bytes = destination_path.stat().st_size
        download_headers["Range"] = f"bytes={existing_bytes}-"
        logger.info(f"Resuming download of {destination_path.name} from byte {existing_bytes}...")

    with requests.get(url, headers=download_headers, stream=True, timeout=30) as response:
        if response.status_code == 416:
            # Requested range not satisfiable - file already completely downloaded
            logger.info(f"File {destination_path.name} already fully downloaded.")
            return destination_path

        response.raise_for_status()

        total_bytes = existing_bytes
        content_len = response.headers.get("Content-Length")
        if content_len:
            total_bytes += int(content_len)

        mode = "ab" if existing_bytes > 0 and response.status_code == 206 else "wb"
        if mode == "wb":
            existing_bytes = 0

        chunk_size = 1024 * 1024  # 1 MB buffer
        downloaded = existing_bytes

        with open(destination_path, mode) as f:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total_bytes:
                        progress_callback(downloaded, total_bytes)

    logger.info(f"Downloaded {destination_path.name} successfully ({downloaded} bytes).")
    return destination_path

