"""
Main entry point for Google Photos to Telegram Migration Engine.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from src.config import config
from src.database import Database
from src.pipeline import MigrationPipeline
from src.telegram_client import TelegramArchiver

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("migration")


async def async_main():
    parser = argparse.ArgumentParser(
        description="Google Photos to Telegram Resumable Migration Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process a folder of downloaded 2 GB Takeout ZIPs:
  python main.py --dir ./data/takeout --delete-after-upload

  # Process Takeout archives sequentially from a text file of URLs:
  python main.py --urls ./data/takeout_urls.txt

  # View migration status:
  python scripts/status.py
        """,
    )
    parser.add_argument(
        "--dir",
        type=Path,
        help="Directory containing Takeout ZIP archives to process sequentially",
    )
    parser.add_argument(
        "--urls",
        type=Path,
        help="File containing one Google Takeout signed download URL per line",
    )
    parser.add_argument(
        "--delete-after-upload",
        action="store_true",
        help="Delete each Takeout ZIP archive after all its contents are uploaded (saves VPS disk)",
    )
    args = parser.parse_args()

    if not args.dir and not args.urls:
        parser.print_help()
        sys.exit(1)

    config.validate_telegram_config()
    db = Database()
    archiver = TelegramArchiver()
    pipeline = MigrationPipeline(db=db, archiver=archiver)

    if args.dir:
        archives_dir = args.dir.resolve()
        if not archives_dir.exists():
            logger.error(f"Directory not found: {archives_dir}")
            sys.exit(1)
        logger.info(f"Starting migration from directory: {archives_dir}")
        await pipeline.run_from_directory(archives_dir, delete_after_upload=args.delete_after_upload)

    elif args.urls:
        urls_file = args.urls.resolve()
        if not urls_file.exists():
            logger.error(f"URLs file not found: {urls_file}")
            sys.exit(1)
        with open(urls_file, "r") as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        logger.info(f"Starting sequential migration for {len(urls)} Takeout chunk URLs")
        await pipeline.run_from_urls(urls)

    logger.info("Migration run finished.")


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()

