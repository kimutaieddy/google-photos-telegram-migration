# Google Photos to Telegram Lossless Archival System

A production-grade, crash-resilient archival pipeline that migrates a Google Photos library (~500 GB) to a private Telegram channel while preserving original files, EXIF metadata, GPS coordinates, descriptions, and album hierarchies.

## Features

* **Lossless Storage:** Uploads media as uncompressed MTProto documents (`force_document=True`) preserving byte-for-byte binary identity.
* **Metadata Preservation:** Integrates image EXIF headers with Google Takeout sidecar `.json` files.
* **Bounded Disk Footprint:** Processes sequential 2 GB Takeout chunks one at a time, comfortably operating on systems with only 4–5 GB free disk space.
* **Bit-for-Bit Verification:** Cryptographic SHA-256 verification against Telegram-hosted copies.
* **Crash Resilient:** SQLite WAL database tracks every file and archive state for immediate resumption after interrupts.
* **VPS Safe:** Hard disk headroom guards protect existing Docker containers (e.g. n8n).

## Quick Start

### 1. Installation
```bash
python -m venv .venv
source .venv/bin/activate  # Or on Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Credentials
Copy `.env.example` to `.env` and fill in:
* `TELEGRAM_API_ID` & `TELEGRAM_API_HASH` (from [my.telegram.org](https://my.telegram.org))
* `TELEGRAM_CHANNEL_ID` (Your private target channel ID)

Generate your `TELEGRAM_SESSION_STRING`:
```bash
python scripts/generate_session.py
```

### 3. Run Proof of Concept
Test the pipeline with simulated assets and verification:
```bash
python scripts/run_poc.py
```

### 4. Run Migration

#### Option A: Direct from Google Drive (Recommended & Fully Automated)
```bash
# 1. One-time OAuth authentication (creates data/gdrive_token.json)
python scripts/auth_gdrive.py

# 2. Automated run: discovers archives, downloads 2 GB chunk, uploads to Telegram, and deletes locally
python main.py --gdrive
```

#### Option B: From a local folder of Takeout ZIPs
```bash
python main.py --dir ./data/takeout --delete-after-upload
```

#### Option C: From a list of download URLs
```bash
python main.py --urls ./data/takeout_urls.txt
```

### 5. Monitor Status & Verify
```bash
python scripts/status.py
python scripts/verify_archive.py --sample 10
```

For complete architectural details, see [HANDOVER.md](file:///d:/scripts/Migration/HANDOVER.md).

