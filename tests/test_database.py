"""
Unit tests for SQLite database operations.
"""

import pytest
from pathlib import Path
from src.database import Database


@pytest.fixture
def temp_db(tmp_path: Path):
    db_file = tmp_path / "test_migration.db"
    return Database(db_file)


def test_database_initialization(temp_db: Database):
    assert temp_db.db_path.exists()
    stats = temp_db.get_migration_stats()
    assert stats.get("total_items") == 0


def test_archive_lifecycle(temp_db: Database):
    arch_id = temp_db.get_or_create_archive("takeout-001.zip", size_bytes=2048)
    assert arch_id > 0

    # Repeat lookup returns same id
    arch_id_2 = temp_db.get_or_create_archive("takeout-001.zip")
    assert arch_id == arch_id_2

    temp_db.update_archive_status(arch_id, "PROCESSING", total_files=10)
    arch = temp_db.get_archive(arch_id)
    assert arch["status"] == "PROCESSING"
    assert arch["total_files"] == 10

    temp_db.update_archive_status(arch_id, "COMPLETED", processed_files=10)
    arch = temp_db.get_archive(arch_id)
    assert arch["status"] == "COMPLETED"
    assert arch["processed_files"] == 10
    assert arch["completed_at"] is not None


def test_media_item_registration_and_deduplication(temp_db: Database):
    arch_id = temp_db.get_or_create_archive("takeout-001.zip")
    item_data = {
        "archive_id": arch_id,
        "relative_path": "Photos from 2024/sample.jpg",
        "filename": "sample.jpg",
        "size_bytes": 1024,
        "sha256": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        "mime_type": "image/jpeg",
        "exif_timestamp": "2024-06-15 12:00:00 UTC",
        "gps_lat": -4.0435,
        "gps_lon": 39.6682,
        "album": "Holiday",
        "description": "Beach vacation",
        "json_sidecar_path": "sample.jpg.json",
    }

    item_id = temp_db.register_media_item(item_data)
    assert item_id > 0

    # Check deduplication query
    dup = temp_db.find_item_by_sha256(item_data["sha256"])
    assert dup is None  # Not uploaded yet

    # Mark uploaded
    temp_db.mark_item_uploaded(item_id, message_id=54321, file_id="file_abc_123", channel_id="-100123")
    dup = temp_db.find_item_by_sha256(item_data["sha256"])
    assert dup is not None
    assert dup["telegram_message_id"] == 54321

    # Mark verified
    temp_db.mark_item_verified(item_id)
    stats = temp_db.get_migration_stats()
    assert stats["verified_items"] == 1
    assert stats["uploaded_items"] == 0  # Changed status to VERIFIED

