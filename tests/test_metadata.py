"""
Unit tests for metadata extraction, SHA-256 calculation, and sidecar merging.
"""

import json
from pathlib import Path
from PIL import Image
from src.metadata import (
    compute_file_sha256,
    find_google_json_sidecar,
    parse_google_json_sidecar,
    derive_album_name,
    extract_full_metadata,
)


def test_compute_file_sha256(tmp_path: Path):
    test_file = tmp_path / "hello.txt"
    test_file.write_bytes(b"hello world")
    # echo -n "hello world" | sha256sum
    # b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9
    digest = compute_file_sha256(test_file)
    assert digest == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"


def test_derive_album_name():
    # Album path
    p1 = Path("Takeout/Google Photos/Trip to Paris/IMG_01.jpg")
    assert derive_album_name(p1) == "Trip to Paris"

    # Standard year folder should not be treated as album
    p2 = Path("Takeout/Google Photos/Photos from 2024/IMG_02.jpg")
    assert derive_album_name(p2) is None

    # Trash should not be treated as album
    p3 = Path("Takeout/Google Photos/Trash/IMG_03.jpg")
    assert derive_album_name(p3) is None


def test_parse_google_json_sidecar(tmp_path: Path):
    sidecar = tmp_path / "photo.jpg.json"
    sidecar_data = {
        "title": "photo.jpg",
        "description": "Family picnic",
        "photoTakenTime": {"timestamp": "1672531199"},
        "geoData": {"latitude": -1.2921, "longitude": 36.8219},
        "people": [{"name": "Alice"}, {"name": "Bob"}],
    }
    sidecar.write_text(json.dumps(sidecar_data), encoding="utf-8")

    meta = parse_google_json_sidecar(sidecar)
    assert meta["description"] == "Family picnic"
    assert meta["lat"] == -1.2921
    assert meta["lon"] == 36.8219
    assert meta["people"] == "Alice, Bob"
    assert "2022-12-31" in meta["timestamp"]


def test_find_google_json_sidecar(tmp_path: Path):
    media = tmp_path / "vacation.jpg"
    media.write_bytes(b"dummy")

    # 1. Exact match
    sidecar1 = tmp_path / "vacation.jpg.json"
    sidecar1.write_text("{}", encoding="utf-8")
    assert find_google_json_sidecar(media) == sidecar1

    # 2. Numbered duplicate quirk: vacation(1).jpg -> vacation.jpg(1).json
    media_dup = tmp_path / "vacation(1).jpg"
    media_dup.write_bytes(b"dummy")
    sidecar_dup = tmp_path / "vacation.jpg(1).json"
    sidecar_dup.write_text("{}", encoding="utf-8")
    assert find_google_json_sidecar(media_dup) == sidecar_dup


def test_extract_full_metadata(tmp_path: Path):
    folder = tmp_path / "Summer 2024"
    folder.mkdir()
    img_path = folder / "test.jpg"

    img = Image.new("RGB", (100, 100), color="blue")
    img.save(img_path)

    sidecar = folder / "test.jpg.json"
    sidecar.write_text(
        json.dumps({
            "description": "Test description",
            "geoData": {"latitude": 10.0, "longitude": 20.0},
        }),
        encoding="utf-8",
    )

    meta = extract_full_metadata(img_path, Path("Takeout/Google Photos/Summer 2024/test.jpg"))
    assert meta.filename == "test.jpg"
    assert meta.album == "Summer 2024"
    assert meta.description == "Test description"
    assert meta.gps_lat == 10.0
    assert meta.gps_lon == 20.0
    assert meta.sha256 is not None

    caption = meta.format_telegram_caption()
    assert "Summer 2024" in caption
    assert "Test description" in caption
    assert "SHA256:" in caption

