import pytest
from src.database import Database

def test_query_and_count_media_items(tmp_path):
    db_file = tmp_path / "test_web.db"
    db = Database(db_path=db_file)

    # Register sample media items
    arch_id = db.get_or_create_archive("takeout-001.zip")
    db.register_media_item({
        "archive_id": arch_id,
        "relative_path": "Photos/img1.jpg",
        "filename": "img1.jpg",
        "size_bytes": 1024,
        "sha256": "abc1",
        "mime_type": "image/jpeg",
        "exif_timestamp": "2023-05-12 10:00:00",
        "gps_lat": None,
        "gps_lon": None,
        "album": "Trip 2023",
        "description": "Vacation photo",
        "json_sidecar_path": None,
    })
    db.register_media_item({
        "archive_id": arch_id,
        "relative_path": "Photos/img2.jpg",
        "filename": "img2.jpg",
        "size_bytes": 2048,
        "sha256": "abc2",
        "mime_type": "image/jpeg",
        "exif_timestamp": "2022-08-01 12:00:00",
        "gps_lat": None,
        "gps_lon": None,
        "album": "Work",
        "description": "Office event",
        "json_sidecar_path": None,
    })

    # Items in PENDING status should not show in web query
    assert db.count_media_items() == 0

    # Mark as UPLOADED with Telegram message IDs
    db.mark_item_uploaded(1, message_id=101, file_id="f101", channel_id="-100123")
    db.mark_item_uploaded(2, message_id=102, file_id="f102", channel_id="-100123")

    # Now should count 2
    assert db.count_media_items() == 2

    # Query all
    items = db.query_media_items(limit=10, offset=0)
    assert len(items) == 2
    # Should be ordered descending by date (2023 first)
    assert items[0]["filename"] == "img1.jpg"
    assert items[1]["filename"] == "img2.jpg"

    # Filter by album
    trip_items = db.query_media_items(album="Trip 2023")
    assert len(trip_items) == 1
    assert trip_items[0]["album"] == "Trip 2023"

    # Search filter
    search_items = db.query_media_items(search="Office")
    assert len(search_items) == 1
    assert search_items[0]["filename"] == "img2.jpg"

    # Year filter
    year_items = db.query_media_items(year="2022")
    assert len(year_items) == 1
    assert year_items[0]["filename"] == "img2.jpg"

    # Album summaries
    albums = db.get_albums_summary()
    assert len(albums) == 2
    album_names = {a["album"] for a in albums}
    assert "Trip 2023" in album_names
    assert "Work" in album_names

    # Years summary
    years = db.get_years_summary()
    assert len(years) == 2
    year_list = [y["year"] for y in years]
    assert "2023" in year_list
    assert "2022" in year_list

