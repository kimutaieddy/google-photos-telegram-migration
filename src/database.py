"""
Persistent SQLite catalog for migration state, deduplication, and verification.
"""

import sqlite3
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from src.config import config


class Database:
    """Thread-safe SQLite database manager with WAL mode."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or config.DB_PATH
        self._ensure_parent_dir()
        self.init_schema()

    def _ensure_parent_dir(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        # Enable WAL mode for high concurrency & crash safety
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def init_schema(self) -> None:
        """Create tables and indexes if they do not exist."""
        with self.get_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS archives (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    archive_name TEXT UNIQUE NOT NULL,
                    source_url TEXT,
                    size_bytes INTEGER,
                    sha256 TEXT,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    total_files INTEGER DEFAULT 0,
                    processed_files INTEGER DEFAULT 0,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS media_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    archive_id INTEGER,
                    relative_path TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    mime_type TEXT,
                    exif_timestamp TEXT,
                    gps_lat REAL,
                    gps_lon REAL,
                    album TEXT,
                    description TEXT,
                    json_sidecar_path TEXT,
                    telegram_message_id INTEGER,
                    telegram_file_id TEXT,
                    telegram_channel_id TEXT,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    retry_count INTEGER DEFAULT 0,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    uploaded_at TIMESTAMP,
                    verified_at TIMESTAMP,
                    FOREIGN KEY (archive_id) REFERENCES archives (id) ON DELETE SET NULL,
                    UNIQUE(archive_id, relative_path)
                );

                CREATE INDEX IF NOT EXISTS idx_media_sha256 ON media_items(sha256);
                CREATE INDEX IF NOT EXISTS idx_media_status ON media_items(status);
                CREATE INDEX IF NOT EXISTS idx_archives_status ON archives(status);
                """
            )

    # -------------------------------------------------------------------------
    # Archive Management
    # -------------------------------------------------------------------------
    def get_or_create_archive(self, archive_name: str, source_url: Optional[str] = None, size_bytes: Optional[int] = None) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM archives WHERE archive_name = ?", (archive_name,))
            row = cursor.fetchone()
            if row:
                return row["id"]
            cursor.execute(
                """
                INSERT INTO archives (archive_name, source_url, size_bytes, status)
                VALUES (?, ?, ?, 'PENDING')
                """,
                (archive_name, source_url, size_bytes),
            )
            return cursor.lastrowid

    def update_archive_status(
        self, archive_id: int, status: str, total_files: Optional[int] = None,
        processed_files: Optional[int] = None, error_message: Optional[str] = None
    ) -> None:
        completed_at = datetime.now(timezone.utc).isoformat() if status == "COMPLETED" else None
        with self.get_connection() as conn:
            query = "UPDATE archives SET status = ?"
            params: List[Any] = [status]
            if total_files is not None:
                query += ", total_files = ?"
                params.append(total_files)
            if processed_files is not None:
                query += ", processed_files = ?"
                params.append(processed_files)
            if error_message is not None:
                query += ", error_message = ?"
                params.append(error_message)
            if completed_at:
                query += ", completed_at = ?"
                params.append(completed_at)
            query += " WHERE id = ?"
            params.append(archive_id)
            conn.execute(query, params)

    def get_archive(self, archive_id: int) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM archives WHERE id = ?", (archive_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    # -------------------------------------------------------------------------
    # Media Item Management
    # -------------------------------------------------------------------------
    def register_media_item(self, item_data: Dict[str, Any]) -> int:
        """Register a media item found inside an archive or directory."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO media_items (
                    archive_id, relative_path, filename, size_bytes, sha256,
                    mime_type, exif_timestamp, gps_lat, gps_lon, album,
                    description, json_sidecar_path, status
                ) VALUES (
                    :archive_id, :relative_path, :filename, :size_bytes, :sha256,
                    :mime_type, :exif_timestamp, :gps_lat, :gps_lon, :album,
                    :description, :json_sidecar_path, 'PENDING'
                )
                ON CONFLICT(archive_id, relative_path) DO UPDATE SET
                    size_bytes = excluded.size_bytes,
                    sha256 = excluded.sha256,
                    exif_timestamp = excluded.exif_timestamp,
                    gps_lat = excluded.gps_lat,
                    gps_lon = excluded.gps_lon,
                    album = excluded.album,
                    description = excluded.description
                """,
                item_data,
            )
            return cursor.lastrowid

    def find_item_by_sha256(self, sha256: str) -> Optional[Dict[str, Any]]:
        """Check if identical file content was already uploaded to avoid duplicates."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM media_items WHERE sha256 = ? AND status IN ('UPLOADED', 'VERIFIED') LIMIT 1",
                (sha256,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_pending_items(self, archive_id: Optional[int] = None, limit: int = 100) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if archive_id is not None:
                cursor.execute(
                    """
                    SELECT * FROM media_items
                    WHERE archive_id = ? AND status IN ('PENDING', 'FAILED') AND retry_count < ?
                    ORDER BY id ASC LIMIT ?
                    """,
                    (archive_id, config.MAX_RETRIES, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT * FROM media_items
                    WHERE status IN ('PENDING', 'FAILED') AND retry_count < ?
                    ORDER BY id ASC LIMIT ?
                    """,
                    (config.MAX_RETRIES, limit),
                )
            return [dict(row) for row in cursor.fetchall()]

    def mark_item_uploaded(
        self, item_id: int, message_id: int, file_id: Optional[str], channel_id: str
    ) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                UPDATE media_items SET
                    status = 'UPLOADED',
                    telegram_message_id = ?,
                    telegram_file_id = ?,
                    telegram_channel_id = ?,
                    uploaded_at = CURRENT_TIMESTAMP,
                    error_message = NULL
                WHERE id = ?
                """,
                (message_id, file_id, channel_id, item_id),
            )

    def mark_item_failed(self, item_id: int, error_message: str) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                UPDATE media_items SET
                    status = 'FAILED',
                    retry_count = retry_count + 1,
                    error_message = ?
                WHERE id = ?
                """,
                (error_message, item_id),
            )

    def mark_item_verified(self, item_id: int) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                UPDATE media_items SET
                    status = 'VERIFIED',
                    verified_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (item_id,),
            )

    def get_migration_stats(self) -> Dict[str, Any]:
        """Return summary statistics of migration progress."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    COUNT(*) as total_items,
                    COALESCE(SUM(CASE WHEN status = 'UPLOADED' THEN 1 ELSE 0 END), 0) as uploaded_items,
                    COALESCE(SUM(CASE WHEN status = 'VERIFIED' THEN 1 ELSE 0 END), 0) as verified_items,
                    COALESCE(SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END), 0) as pending_items,
                    COALESCE(SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END), 0) as failed_items,
                    COALESCE(SUM(size_bytes), 0) as total_bytes,
                    COALESCE(SUM(CASE WHEN status IN ('UPLOADED', 'VERIFIED') THEN size_bytes ELSE 0 END), 0) as uploaded_bytes
                FROM media_items
                """
            )
            row = cursor.fetchone()
            stats = dict(row) if row else {}
            cursor.execute(
                """
                SELECT
                    COUNT(*) as total_archives,
                    COALESCE(SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END), 0) as completed_archives
                FROM archives
                """
            )
            arch_row = cursor.fetchone()
            if arch_row:
                stats.update(dict(arch_row))
            return stats

    # -------------------------------------------------------------------------
    # Web Library Queries
    # -------------------------------------------------------------------------
    def query_media_items(
        self,
        album: Optional[str] = None,
        search: Optional[str] = None,
        year: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Query uploaded/verified media items with filtering and pagination."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            conditions = ["status IN ('UPLOADED', 'VERIFIED')"]
            params: List[Any] = []

            if album:
                conditions.append("album = ?")
                params.append(album)

            if search:
                conditions.append("(filename LIKE ? OR description LIKE ? OR album LIKE ?)")
                wildcard = f"%{search}%"
                params.extend([wildcard, wildcard, wildcard])

            if year:
                conditions.append("exif_timestamp LIKE ?")
                params.append(f"{year}%")

            where_clause = " AND ".join(conditions)
            query = f"""
                SELECT id, relative_path, filename, size_bytes, sha256,
                       mime_type, exif_timestamp, gps_lat, gps_lon, album,
                       description, telegram_message_id, telegram_file_id,
                       status, uploaded_at
                FROM media_items
                WHERE {where_clause}
                ORDER BY COALESCE(exif_timestamp, uploaded_at, '1970-01-01') DESC, id DESC
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def count_media_items(
        self,
        album: Optional[str] = None,
        search: Optional[str] = None,
        year: Optional[str] = None,
    ) -> int:
        """Count total media items matching filters."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            conditions = ["status IN ('UPLOADED', 'VERIFIED')"]
            params: List[Any] = []

            if album:
                conditions.append("album = ?")
                params.append(album)

            if search:
                conditions.append("(filename LIKE ? OR description LIKE ? OR album LIKE ?)")
                wildcard = f"%{search}%"
                params.extend([wildcard, wildcard, wildcard])

            if year:
                conditions.append("exif_timestamp LIKE ?")
                params.append(f"{year}%")

            where_clause = " AND ".join(conditions)
            cursor.execute(f"SELECT COUNT(*) FROM media_items WHERE {where_clause}", params)
            return cursor.fetchone()[0]

    def get_media_item(self, item_id: int) -> Optional[Dict[str, Any]]:
        """Get single media item by ID."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM media_items WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_albums_summary(self) -> List[Dict[str, Any]]:
        """List distinct albums with photo counts and latest cover photo."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    album,
                    COUNT(*) as count,
                    MAX(id) as cover_item_id,
                    MAX(COALESCE(exif_timestamp, uploaded_at)) as latest_date
                FROM media_items
                WHERE status IN ('UPLOADED', 'VERIFIED') AND album IS NOT NULL AND album != ''
                GROUP BY album
                ORDER BY count DESC, album ASC
                """
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_years_summary(self) -> List[Dict[str, Any]]:
        """List available years based on timestamps."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    SUBSTR(COALESCE(exif_timestamp, uploaded_at), 1, 4) as year,
                    COUNT(*) as count
                FROM media_items
                WHERE status IN ('UPLOADED', 'VERIFIED')
                  AND COALESCE(exif_timestamp, uploaded_at) IS NOT NULL
                  AND SUBSTR(COALESCE(exif_timestamp, uploaded_at), 1, 4) GLOB '[1-2][0-9][0-9][0-9]'
                GROUP BY year
                ORDER BY year DESC
                """
            )
            return [dict(row) for row in cursor.fetchall()]

