"""
Metadata extraction, sidecar parsing, EXIF handling, and SHA-256 calculation.
"""

import hashlib
import json
import mimetypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from PIL import Image, ExifTags


@dataclass
class MediaMetadata:
    """Consolidated metadata for a media asset."""
    filename: str
    relative_path: str
    size_bytes: int
    sha256: str
    mime_type: Optional[str] = None
    exif_timestamp: Optional[str] = None
    gps_lat: Optional[float] = None
    gps_lon: Optional[float] = None
    album: Optional[str] = None
    description: Optional[str] = None
    people: Optional[str] = None
    json_sidecar_path: Optional[str] = None

    def format_telegram_caption(self, max_length: int = 1024) -> str:
        """Format a concise, readable caption for Telegram."""
        lines = [f"📁 <b>{self.filename}</b>"]

        if self.album:
            lines.append(f"🏷️ <i>Album:</i> {self.album}")

        if self.exif_timestamp:
            lines.append(f"📅 <i>Taken:</i> {self.exif_timestamp}")

        if self.gps_lat is not None and self.gps_lon is not None:
            lines.append(f"📍 <i>Location:</i> {self.gps_lat:.5f}, {self.gps_lon:.5f}")

        if self.people:
            lines.append(f"👥 <i>People:</i> {self.people}")

        if self.description:
            desc = self.description.strip()
            if len(desc) > 200:
                desc = desc[:197] + "..."
            lines.append(f"📝 {desc}")

        # Add truncated SHA-256 fingerprint for quick verification
        lines.append(f"🔒 <code>SHA256:{self.sha256[:16]}...</code>")

        caption = "\n".join(lines)
        if len(caption) > max_length:
            return caption[: max_length - 3] + "..."
        return caption


def compute_file_sha256(file_path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Compute SHA-256 checksum in a memory-bounded streaming fashion."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def _convert_dms_to_deg(dms, ref) -> Optional[float]:
    """Convert GPS degrees, minutes, seconds tuple to decimal degrees."""
    try:
        degrees = float(dms[0])
        minutes = float(dms[1]) / 60.0
        seconds = float(dms[2]) / 3600.0
        val = degrees + minutes + seconds
        if ref in ("S", "W"):
            val = -val
        return val
    except Exception:
        return None


def extract_exif_from_image(file_path: Path) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    """Extract capture timestamp and GPS from image file headers."""
    timestamp = None
    lat = None
    lon = None

    try:
        with Image.open(file_path) as img:
            exif_data = img._getexif()
            if not exif_data:
                return None, None, None

            # Map tag IDs to tag names
            tags = {ExifTags.TAGS.get(k, k): v for k, v in exif_data.items()}

            # Extract DateTimeOriginal
            date_str = tags.get("DateTimeOriginal") or tags.get("DateTime")
            if date_str:
                try:
                    # Format standard EXIF 'YYYY:MM:DD HH:MM:SS'
                    dt = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
                    timestamp = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
                except Exception:
                    timestamp = str(date_str)

            # Extract GPS Info
            gps_info = tags.get("GPSInfo")
            if gps_info and isinstance(gps_info, dict):
                gps_tags = {ExifTags.GPSTAGS.get(k, k): v for k, v in gps_info.items()}
                lat_dms = gps_tags.get("GPSLatitude")
                lat_ref = gps_tags.get("GPSLatitudeRef")
                lon_dms = gps_tags.get("GPSLongitude")
                lon_ref = gps_tags.get("GPSLongitudeRef")

                if lat_dms and lat_ref:
                    lat = _convert_dms_to_deg(lat_dms, lat_ref)
                if lon_dms and lon_ref:
                    lon = _convert_dms_to_deg(lon_dms, lon_ref)

    except Exception:
        # Not a valid image or EXIF could not be parsed
        pass

    return timestamp, lat, lon


def find_google_json_sidecar(media_path: Path) -> Optional[Path]:
    """
    Locate the Google Photos Takeout JSON sidecar for a given media file.
    Handles standard '.ext.json', truncated extensions, and numbered duplicates.
    """
    parent = media_path.parent
    name = media_path.name

    # 1. Standard pattern: IMG_001.JPG.json
    cand1 = parent / f"{name}.json"
    if cand1.exists():
        return cand1

    # 2. Numbered duplicate quirk: IMG_001(1).JPG -> IMG_001.JPG(1).json
    if "(" in media_path.stem and ")" in media_path.stem:
        stem = media_path.stem
        ext = media_path.suffix
        # e.g., stem="photo(1)", ext=".jpg"
        prefix = stem[: stem.rfind("(")]
        suffix_num = stem[stem.rfind("(") :]
        cand2 = parent / f"{prefix}{ext}{suffix_num}.json"
        if cand2.exists():
            return cand2

    # 3. Truncated extension quirk (Google Takeout truncates filenames over 51 chars):
    # e.g. very_long_name_12345.jpe.json
    for cand in parent.glob(f"{media_path.stem[:40]}*.json"):
        if cand != media_path:
            return cand

    return None


def parse_google_json_sidecar(json_path: Path) -> Dict[str, Any]:
    """Parse Google Takeout JSON sidecar dictionary."""
    result: Dict[str, Any] = {}
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Description
        desc = data.get("description")
        if desc:
            result["description"] = desc

        # photoTakenTime
        photo_taken = data.get("photoTakenTime", {})
        ts_str = photo_taken.get("timestamp")
        if ts_str:
            try:
                dt = datetime.fromtimestamp(int(ts_str), tz=timezone.utc)
                result["timestamp"] = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
            except Exception:
                pass

        # GeoData (prefer geoData, fallback to geoDataExif)
        geo = data.get("geoData") or data.get("geoDataExif") or {}
        lat = geo.get("latitude")
        lon = geo.get("longitude")
        if lat and lon and (lat != 0.0 or lon != 0.0):
            result["lat"] = float(lat)
            result["lon"] = float(lon)

        # People
        people = data.get("people", [])
        if people and isinstance(people, list):
            names = [p.get("name") for p in people if isinstance(p, dict) and p.get("name")]
            if names:
                result["people"] = ", ".join(names)

    except Exception:
        pass

    return result


def derive_album_name(relative_path: Path) -> Optional[str]:
    """
    Derive Google Photos Album from path hierarchy.
    Takeout format: Takeout/Google Photos/[Album Name]/photo.jpg
    Ignores system/date folders like 'Photos from 2024' or 'Trash'.
    """
    parts = relative_path.parts
    if len(parts) >= 2:
        # Immediate parent folder
        folder = parts[-2]
        if not folder.startswith("Photos from ") and folder.lower() not in ("trash", "archive", "google photos"):
            return folder
    return None


def extract_full_metadata(file_path: Path, relative_path: Path) -> MediaMetadata:
    """Extract complete metadata by merging file EXIF, Google JSON sidecar, and path info."""
    filename = file_path.name
    size_bytes = file_path.stat().st_size
    sha256 = compute_file_sha256(file_path)
    mime_type, _ = mimetypes.guess_type(filename)

    album = derive_album_name(relative_path)

    # 1. Extract from EXIF
    exif_ts, exif_lat, exif_lon = extract_exif_from_image(file_path)

    # 2. Check for Google Takeout JSON sidecar
    sidecar_path = find_google_json_sidecar(file_path)
    json_meta = parse_google_json_sidecar(sidecar_path) if sidecar_path else {}

    # 3. Merge: Google Photos JSON values override or complement EXIF
    final_ts = json_meta.get("timestamp") or exif_ts
    final_lat = json_meta.get("lat") or exif_lat
    final_lon = json_meta.get("lon") or exif_lon
    description = json_meta.get("description")
    people = json_meta.get("people")

    return MediaMetadata(
        filename=filename,
        relative_path=str(relative_path).replace("\\", "/"),
        size_bytes=size_bytes,
        sha256=sha256,
        mime_type=mime_type,
        exif_timestamp=final_ts,
        gps_lat=final_lat,
        gps_lon=final_lon,
        album=album,
        description=description,
        people=people,
        json_sidecar_path=str(sidecar_path.relative_to(file_path.parent)) if sidecar_path else None,
    )

