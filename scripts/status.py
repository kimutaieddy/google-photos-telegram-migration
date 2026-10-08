"""
Migration status dashboard and progress reporting.
"""

import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.config import config
from src.database import Database


def format_bytes(b: int | float) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024.0:
            return f"{b:.2f} {unit}"
        b /= 1024.0
    return f"{b:.2f} PB"


def main():
    db = Database()
    stats = db.get_migration_stats()

    # Disk usage
    total_disk, used_disk, free_disk = shutil.disk_usage(config.DATA_DIR)

    print("\n" + "=" * 65)
    print(" 🚀 GOOGLE PHOTOS TO TELEGRAM MIGRATION STATUS")
    print("=" * 65)

    print("\n📦 ARCHIVE PROGRESS:")
    print(f"  Total Takeout Archives:     {stats.get('total_archives', 0)}")
    print(f"  Completed Archives:         {stats.get('completed_archives', 0)}")

    print("\n🖼️ MEDIA ITEMS:")
    total_items = stats.get("total_items", 0)
    uploaded = stats.get("uploaded_items", 0)
    verified = stats.get("verified_items", 0)
    pending = stats.get("pending_items", 0)
    failed = stats.get("failed_items", 0)

    print(f"  Total Items Cataloged:      {total_items}")
    print(f"  Uploaded to Telegram:       {uploaded}")
    print(f"  Verified Bit-for-Bit:       {verified}")
    print(f"  Pending:                    {pending}")
    print(f"  Failed:                     {failed}")

    print("\n📊 DATA VOLUME:")
    tot_bytes = stats.get("total_bytes") or 0
    up_bytes = stats.get("uploaded_bytes") or 0
    pct = (up_bytes / tot_bytes * 100) if tot_bytes > 0 else 0.0
    print(f"  Total Library Volume:       {format_bytes(tot_bytes)}")
    print(f"  Archived to Telegram:       {format_bytes(up_bytes)} ({pct:.1f}%)")

    print("\n💾 VPS DISK USAGE:")
    print(f"  Directory:                  {config.DATA_DIR}")
    print(f"  Free Space:                 {format_bytes(free_disk)}")
    print(f"  Total Disk:                 {format_bytes(total_disk)}")
    print(f"  Minimum Safety Buffer:      {config.MIN_DISK_FREE_MB} MB")

    if failed > 0:
        print("\n⚠️ RECENT FAILED ITEMS:")
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT filename, error_message, retry_count FROM media_items WHERE status = 'FAILED' LIMIT 5")
            for row in cur.fetchall():
                print(f"  - {row['filename']} (retries: {row['retry_count']}): {row['error_message']}")

    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()

