"""
Configuration management with environment validation and disk safety bounds.
"""

import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Load .env file from project root if it exists
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


class Config:
    """System configuration singleton."""

    PROJECT_ROOT: Path = PROJECT_ROOT

    # Telegram MTProto Credentials
    TELEGRAM_API_ID: int = int(os.getenv("TELEGRAM_API_ID", "0"))
    TELEGRAM_API_HASH: str = os.getenv("TELEGRAM_API_HASH", "")
    TELEGRAM_SESSION_STRING: Optional[str] = os.getenv("TELEGRAM_SESSION_STRING") or None
    TELEGRAM_SESSION_NAME: str = os.getenv("TELEGRAM_SESSION_NAME", "telegram_migration")

    # Target Telegram Destination
    # Can be channel ID like -1001234567890 or username @my_channel
    _raw_channel_id = os.getenv("TELEGRAM_CHANNEL_ID", "")
    TELEGRAM_CHANNEL_ID: int | str = (
        int(_raw_channel_id) if _raw_channel_id.lstrip("-").isdigit() else _raw_channel_id
    )

    # Optional Bot Token for alerts
    BOT_TOKEN: Optional[str] = os.getenv("BOT_TOKEN") or None

    # Storage paths
    _raw_data_dir = os.getenv("DATA_DIR", "data")
    DATA_DIR: Path = (
        (PROJECT_ROOT / _raw_data_dir).resolve()
        if not Path(_raw_data_dir).is_absolute()
        else Path(_raw_data_dir).resolve()
    )
    _raw_temp_dir = os.getenv("TEMP_DIR")
    TEMP_DIR: Path = (
        Path(_raw_temp_dir).resolve()
        if _raw_temp_dir
        else DATA_DIR / "temp"
    )
    THUMBNAIL_DIR: Path = DATA_DIR / "thumbnails"
    DB_PATH: Path = DATA_DIR / "migration.db"

    # Web Interface
    WEB_PORT: int = int(os.getenv("WEB_PORT", "8055"))
    WEB_HOST: str = os.getenv("WEB_HOST", "0.0.0.0")
    WEB_AUTH_TOKEN: str = os.getenv("WEB_AUTH_TOKEN", "")

    # Disk space threshold in MB (stop/pause if free space is below this)
    MIN_DISK_FREE_MB: int = int(os.getenv("MIN_DISK_FREE_MB", "1500"))

    # Upload & Rate-limit constraints
    UPLOAD_DELAY_SECONDS: float = float(os.getenv("UPLOAD_DELAY_SECONDS", "2.5"))
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "5"))
    
    # 2 GB default for standard Telegram user accounts
    MAX_FILE_SIZE_BYTES: int = int(os.getenv("MAX_FILE_SIZE_BYTES", str(2 * 1024 * 1024 * 1024)))

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary data and temp directories if they do not exist."""
        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)
        cls.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        cls.THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def validate_telegram_config(cls) -> None:
        """Validate that minimum required Telegram credentials are present."""
        errors = []
        if not cls.TELEGRAM_API_ID:
            errors.append("TELEGRAM_API_ID is required (get from https://my.telegram.org)")
        if not cls.TELEGRAM_API_HASH:
            errors.append("TELEGRAM_API_HASH is required")
        if not cls.TELEGRAM_CHANNEL_ID:
            errors.append("TELEGRAM_CHANNEL_ID is required (e.g. -100xxxxxxxxxx)")
        if errors:
            raise ValueError(f"Configuration Errors:\n" + "\n".join(f"- {e}" for e in errors))


config = Config()

