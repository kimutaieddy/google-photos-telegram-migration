"""
CLI runner for Phase 4: Cryptographic verification of Telegram uploads.
"""

import sys
import argparse
import asyncio
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import config
from src.database import Database
from src.telegram_client import TelegramArchiver
from src.verifier import IntegrityVerifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


async def main():
    parser = argparse.ArgumentParser(description="Verify Telegram uploads bit-for-bit against SQLite catalog.")
    parser.add_argument("--sample", type=int, default=5, help="Number of random uploaded items to verify (default: 5)")
    parser.add_argument("--item-id", type=int, help="Verify a specific media item by its database ID")
    parser.add_argument("--all", action="store_true", help="Verify all uploaded items in the database")
    args = parser.parse_args()

    config.validate_telegram_config()
    db = Database()
    archiver = TelegramArchiver()
    verifier = IntegrityVerifier(db=db, archiver=archiver)

    await archiver.connect()

    try:
        if args.item_id:
            logger.info(f"Verifying single item ID: {args.item_id}")
            res = await verifier.verify_item(args.item_id)
            print(f"\nResult for {res['filename']}: {'PASSED' if res['success'] else 'FAILED'}")
            print(f"  Source SHA-256:   {res['source_sha256']}")
            print(f"  Telegram SHA-256: {res['telegram_sha256']}")

        elif args.all:
            with db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT id FROM media_items WHERE status = 'UPLOADED'")
                ids = [r["id"] for r in cur.fetchall()]

            logger.info(f"Verifying all {len(ids)} uploaded items...")
            passed = 0
            for item_id in ids:
                res = await verifier.verify_item(item_id)
                if res["success"]:
                    passed += 1
            print(f"\nVerification Complete: {passed}/{len(ids)} items verified successfully.")

        else:
            logger.info(f"Running spot-check verification on {args.sample} random items...")
            results = await verifier.verify_sample(sample_size=args.sample)
            passed = sum(1 for r in results if r["success"])
            print(f"\nSpot Check Complete: {passed}/{len(results)} sampled items verified successfully.")

    finally:
        await archiver.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

