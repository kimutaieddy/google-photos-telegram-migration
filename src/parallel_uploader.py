"""
High-performance parallel MTProto uploader for Telethon.
Uploads multiple 512KB chunks concurrently over MTProto, eliminating
sequential round-trip latency and utilizing full uplink bandwidth.
"""

import asyncio
import hashlib
import math
import os
from pathlib import Path
from typing import Optional, Callable
from telethon import TelegramClient, custom, helpers, types, utils
from telethon.tl import functions


async def upload_file_parallel(
    client: TelegramClient,
    file_path: Path,
    part_size_kb: int = 512,
    max_workers: int = 8,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> types.TypeInputFile:
    """
    Upload a file to Telegram using parallel MTProto chunk requests.
    Supports up to max_workers concurrent connections/requests.
    """
    file_path = Path(file_path)
    file_size = file_path.stat().st_size
    file_name = file_path.name
    part_size = part_size_kb * 1024

    part_count = (file_size + part_size - 1) // part_size
    is_big = file_size > 10 * 1024 * 1024
    file_id = helpers.generate_random_long()
    hash_md5 = hashlib.md5() if not is_big else None

    # Track uploaded bytes for progress callback
    uploaded_bytes = 0
    lock = asyncio.Lock()

    # Semaphore to limit simultaneous chunk transmissions
    semaphore = asyncio.Semaphore(max_workers)

    async def _upload_part(part_index: int, offset: int, length: int):
        nonlocal uploaded_bytes
        # Read the slice directly from disk
        with open(file_path, "rb") as f:
            f.seek(offset)
            chunk = f.read(length)

        if not is_big:
            # MD5 calculation in order will be done before or after, but for small files part_count is usually 1-20
            pass

        async with semaphore:
            retries = 0
            while retries < 5:
                try:
                    if is_big:
                        req = functions.upload.SaveBigFilePartRequest(
                            file_id=file_id,
                            file_part=part_index,
                            file_total_parts=part_count,
                            bytes=chunk,
                        )
                    else:
                        req = functions.upload.SaveFilePartRequest(
                            file_id=file_id,
                            file_part=part_index,
                            bytes=chunk,
                        )
                    res = await asyncio.wait_for(client(req), timeout=60.0)
                    if res:
                        async with lock:
                            uploaded_bytes += length
                            if progress_callback:
                                await helpers._maybe_await(progress_callback(uploaded_bytes, file_size))
                        return
                    else:
                        raise RuntimeError(f"Failed to upload part {part_index}")
                except Exception as e:
                    retries += 1
                    if retries >= 5:
                        raise e
                    await asyncio.sleep(0.5 * retries)

    # For small files under 10MB, calculate MD5 hash upfront
    if not is_big:
        with open(file_path, "rb") as f:
            while chunk := f.read(part_size):
                hash_md5.update(chunk)

    tasks = []
    for i in range(part_count):
        offset = i * part_size
        length = min(part_size, file_size - offset)
        tasks.append(_upload_part(i, offset, length))

    # Execute uploads in parallel
    await asyncio.gather(*tasks)

    if is_big:
        return types.InputFileBig(file_id, part_count, file_name)
    else:
        return custom.InputSizedFile(
            file_id, part_count, file_name, md5=hash_md5, size=file_size
        )

