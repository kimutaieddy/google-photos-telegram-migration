# Google Photos to Telegram Archival System — Comprehensive Handover Manual (`HANDOVER.md`)

This document is the definitive operational and technical reference for developers and system administrators taking over this production-grade archival project.

---

## 1. Executive Summary & Problem Context

* **Source Library:** ~500 GB of personal photos and 4K videos hosted in Google Photos under an active 5 TB Google One subscription expiring imminently.
* **Target Destination:** A private Telegram channel configured as a permanent, searchable, lossless cloud archive.
* **Execution Options:**
  1. **Azure VPS (Ubuntu 24.04, Standard_B4as_v2, 4 vCPU, 16 GB RAM, 30 GB OS disk with ~4–5 GB free).**
  2. **Standalone Dedicated Laptop/Server (Local network, bypasses Azure bandwidth costs entirely).**
* **Constraints Met:**
  * **Zero data loss & byte-for-byte fidelity:** Uncompressed document uploads (`force_document=True`).
  * **Bounded disk footprint:** Operates safely in $< 2.5\text{ GB}$ peak disk by processing 2 GB Takeout chunks sequentially and deleting temporary extractions immediately.
  * **Complete metadata preservation:** Merges EXIF headers (camera, ISO, date, GPS) with Google Takeout sidecar `.json` files (custom descriptions, edited timestamps, face/people tags, album assignments).
  * **Resumability & Crash Recovery:** Atomic SQLite transactions in WAL mode track archive and file states (`PENDING`, `PROCESSING`, `UPLOADED`, `VERIFIED`, `FAILED`).
  * **Non-destructive VPS deployment:** Strict Docker/container resource limits and isolated paths ensure zero conflict with existing services (e.g., n8n).

---

## 2. Architecture & Technology Rationale

```
┌────────────────────────┐
│     Google Photos      │ (500 GB)
└───────────┬────────────┘
            │ Google Takeout Export (2 GB ZIP chunks)
            ▼
┌────────────────────────────────────────────────────────┐
│          Migration Engine (VPS or Laptop)              │
│                                                        │
│  ┌──────────────────┐       ┌───────────────────────┐  │
│  │ Downloader       │──────▶│ Sequential ZIP Parser │  │
│  │ (Resumable Range)│       │ (Bounded ~100MB temp) │  │
│  └──────────────────┘       └───────────┬───────────┘  │
│                                         │              │
│  ┌──────────────────────────────────────▼───────────┐  │
│  │ Metadata Engine                                  │  │
│  │ • Chunked SHA-256 calculation                    │  │
│  │ • EXIF extraction (Pillow)                       │  │
│  │ • Takeout JSON sidecar parser & merger           │  │
│  │ • Album hierarchy derivation                     │  │
│  └──────────────────┬───────────────────────────────┘  │
│                     ▼                                  │
│  ┌──────────────────────────────────────────────────┐  │
│  │ SQLite Catalog (migration.db - WAL Mode)         │  │
│  │ • Deduplication by SHA-256                       │  │
│  │ • Resumable file/archive state tracking          │  │
│  └──────────────────┬───────────────────────────────┘  │
│                     │                                  │
│  ┌──────────────────▼───────────────────────────────┐  │
│  │ Telegram MTProto Client (Telethon)               │  │
│  │ • Uncompressed Document Upload (Lossless)        │  │
│  │ • Rate limiter + FloodWait backoff               │  │
│  │ • Rich HTML metadata caption                     │  │
│  └──────────────────┬───────────────────────────────┘  │
└─────────────────────┼──────────────────────────────────┘
                      │ MTProto TLS
                      ▼
┌────────────────────────────────────────────────────────┐
│           Private Telegram Channel Archive             │
│   (Searchable, lossless files + metadata captions)     │
└────────────────────────────────────────────────────────┘
```

### Why These Technologies Were Selected:
1. **Google Takeout instead of Google Photos API:**
   * On **March 31, 2025**, Google disabled `photoslibrary.readonly`. The API only returns photos created by the calling app.
   * Google Takeout is the only supported mechanism providing original binaries and user metadata sidecars.
2. **Telethon (MTProto) instead of Telegram Bot API:**
   * Telegram Bot API enforces a **50 MB upload limit**.
   * Telethon connects directly via MTProto under the user's account, allowing files up to **2 GB** (and up to 4 GB with Telegram Premium).
3. **Lossless Documents (`send_file(force_document=True)`):**
   * Normal photo/video uploads trigger Telegram's compression servers, re-encoding images to 1280px/2560px JPEG and stripping EXIF. Sending as a document guarantees byte-for-byte binary identity.
4. **SQLite with Write-Ahead Logging (WAL):**
   * Provides single-file zero-configuration persistence with robust crash recovery.
   * Enables concurrent reader operations (e.g. status monitoring scripts) while uploads write to the catalog.

---

## 3. Directory Structure

```
Migration/
├── .env.example          # Sample environment variables
├── .gitignore            # Protects credentials, sessions, and databases
├── requirements.txt      # Python dependencies
├── Dockerfile            # Container definition
├── docker-compose.yml    # Isolated deployment config
├── main.py               # Main CLI entrypoint
├── HANDOVER.md           # This document
├── README.md             # Quickstart guide
├── src/
│   ├── __init__.py
│   ├── config.py         # Config management & directory validation
│   ├── database.py       # SQLite WAL database catalog
│   ├── metadata.py       # SHA-256, EXIF, and JSON sidecar engine
│   ├── telegram_client.py# Telethon MTProto wrapper & rate limiter
│   ├── downloader.py     # Resumable downloader with disk headroom enforcement
│   ├── pipeline.py       # Orchestrator & sequential archive processor
│   └── verifier.py       # Cryptographic byte-for-byte verifier
├── scripts/
│   ├── generate_session.py # Interactive Telethon StringSession creator
│   ├── run_poc.py          # Phase 3 & 4 Proof of Concept runner
│   ├── status.py           # Real-time CLI migration dashboard
│   └── verify_archive.py   # CLI verification runner
└── tests/
    ├── __init__.py
    ├── test_database.py    # Database unit tests
    └── test_metadata.py    # Metadata & hashing unit tests
```

---

## 4. Setup & Authentication Guide

### Step 1: Telegram Credentials
1. Log in to [my.telegram.org](https://my.telegram.org) with your Telegram phone number.
2. Navigate to **API development tools** and create an application.
3. Note your `api_id` (numeric) and `api_hash` (string).
4. Create a **Private Channel** in Telegram (or use an existing one).
5. Obtain the Channel ID (e.g., `-100xxxxxxxxxx`). Tip: Forward a message from the channel to `@userinfobot` or `@RawDataBot` to find the exact ID.

### Step 2: Generate Session String (Headless / Docker Setup)
Run the session generator once locally:
```bash
python scripts/generate_session.py
```
Enter your phone number and login code. It will output a `TELEGRAM_SESSION_STRING`.

### Step 3: Populate `.env`
Create `.env` in the project root:
```ini
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef0123456789abcdef0123456789
TELEGRAM_CHANNEL_ID=-1001234567890
TELEGRAM_SESSION_STRING=1BJWap1wBu...
MIN_DISK_FREE_MB=1500
UPLOAD_DELAY_SECONDS=2.5
```

---

## 5. Google Export Procedure

1. Go to [takeout.google.com](https://takeout.google.com).
2. Deselect all services, then select **only Google Photos**.
3. Choose archive format: **.zip**.
4. Choose maximum file size: **2 GB**. *(Crucial for the 4–5 GB VPS disk constraint)*.
5. Google will send an email when the archives are ready (typically 24–48 hours for 500 GB).
6. When complete, copy the download links.

---

## 6. Running the Migration

### Option A: From a local directory of ZIP archives (Laptop or Server)
```bash
python main.py --dir /path/to/takeout_zips --delete-after-upload
```

### Option B: Directly from Takeout download URLs (VPS)
Create a file `data/takeout_urls.txt` with one signed Google download URL per line:
```bash
python main.py --urls ./data/takeout_urls.txt
```
The engine will:
1. Ensure at least 2.5 GB free disk.
2. Download chunk `takeout-chunk-001.zip`.
3. Extract and upload files one by one to Telegram.
4. Mark archive as complete in SQLite.
5. Delete `takeout-chunk-001.zip` from disk.
6. Proceed to `takeout-chunk-002.zip`.

### Monitoring Progress
Run in a separate terminal:
```bash
python scripts/status.py
```

---

## 7. Cryptographic Verification (Phase 4)

To verify that Telegram copies are identical to source files without downloading the entire 500 GB library at once:

```bash
# Verify 10 random sampled files
python scripts/verify_archive.py --sample 10

# Verify a specific item by its database ID
python scripts/verify_archive.py --item-id 42

# Verify all uploaded files (requires sufficient bandwidth/time)
python scripts/verify_archive.py --all
```

The verifier downloads the document from Telegram, computes SHA-256, compares it to `media_items.sha256`, and records the result.

---

## 8. Failure Recovery & Resumability

* **Process Crash / Server Reboot:** Simply re-run `python main.py ...`. The engine queries `archives` and `media_items` to skip all completed files and resumes immediately at the last pending item.
* **Network Disconnection / Rate Limits:** The engine catches `FloodWaitError` and sleeps automatically for the exact duration requested by Telegram's servers.
* **Failed Items:** Failed items are logged with their exact error message and retried up to `MAX_RETRIES` (default 5).

---

## 9. Safeguards for Existing VPS Services (e.g. n8n)

* **Disk Exhaustion Guard:** `src/downloader.py` checks free space before every operation. If free space is below `MIN_DISK_FREE_MB` (1.5 GB), it immediately halts before filling the disk, protecting n8n's SQLite/Postgres database.
* **Process Isolation:** The application runs in its own virtual environment or isolated Docker container with strict CPU and RAM limits (`cpus: 2.0`, `memory: 4096M`).

