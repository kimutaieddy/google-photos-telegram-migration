"""
FastAPI Backend for Google Photos-style Web Library.
Serves photos catalog from SQLite and streams media dynamically from Telegram MTProto.
"""

import os
import mimetypes
from pathlib import Path
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Query, Security, Depends
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from src.config import config
from src.database import Database
from src.telegram_bridge import bridge

app = FastAPI(
    title="CloudWest Photos",
    description="Personal Google Photos-style library backed by Telegram cloud storage",
    version="1.0.0",
)

db = Database()

# Optional simple API Key protection if WEB_AUTH_TOKEN is configured
api_key_header = APIKeyHeader(name="X-Auth-Token", auto_error=False)


def verify_auth(api_key: Optional[str] = Security(api_key_header)):
    if config.WEB_AUTH_TOKEN and api_key != config.WEB_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True


@app.on_event("startup")
async def startup_event():
    config.ensure_directories()
    # Pre-connect Telegram bridge
    try:
        await bridge.connect()
    except Exception as e:
        print(f"Warning: Could not connect TelegramBridge at startup: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    await bridge.disconnect()


# -----------------------------------------------------------------------------
# API Endpoints
# -----------------------------------------------------------------------------

@app.get("/api/stats")
async def get_stats(_: bool = Depends(verify_auth)):
    """Summary of library contents."""
    return db.get_migration_stats()


@app.get("/api/albums")
async def get_albums(_: bool = Depends(verify_auth)):
    """List distinct albums and cover items."""
    return db.get_albums_summary()


@app.get("/api/years")
async def get_years(_: bool = Depends(verify_auth)):
    """List available timeline years."""
    return db.get_years_summary()


@app.get("/api/photos")
async def list_photos(
    album: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    year: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: bool = Depends(verify_auth),
):
    """Paginated list of photos with filters."""
    items = db.query_media_items(
        album=album, search=search, year=year, limit=limit, offset=offset
    )
    total = db.count_media_items(album=album, search=search, year=year)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
    }


@app.get("/api/photos/{photo_id}")
async def get_photo(photo_id: int, _: bool = Depends(verify_auth)):
    """Get metadata for a single photo."""
    item = db.get_media_item(photo_id)
    if not item:
        raise HTTPException(status_code=404, detail="Photo not found")
    return item


@app.get("/api/photos/{photo_id}/thumb")
async def get_photo_thumbnail(photo_id: int):
    """
    Serve cached thumbnail or fetch on-demand from Telegram.
    Returns JPEG thumbnail.
    """
    item = db.get_media_item(photo_id)
    if not item or not item.get("telegram_message_id"):
        raise HTTPException(status_code=404, detail="Photo not found")

    msg_id = item["telegram_message_id"]
    thumb_bytes = await bridge.get_thumbnail_bytes(msg_id)
    if not thumb_bytes:
        # Fallback 1x1 transparent GIF or empty response
        raise HTTPException(status_code=404, detail="Thumbnail not available")

    return Response(
        content=thumb_bytes,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )


@app.get("/api/photos/{photo_id}/full")
async def get_full_photo(photo_id: int):
    """
    Stream full uncompressed original photo/video directly from Telegram MTProto.
    Zero local VPS disk consumption.
    """
    item = db.get_media_item(photo_id)
    if not item or not item.get("telegram_message_id"):
        raise HTTPException(status_code=404, detail="Photo not found")

    msg_id = item["telegram_message_id"]
    filename = item.get("filename", f"photo_{photo_id}")
    mime_type = item.get("mime_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream"

    try:
        stream = bridge.stream_full_media(msg_id)
        return StreamingResponse(
            stream,
            media_type=mime_type,
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                "Content-Length": str(item["size_bytes"]),
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to stream media: {e}")


# -----------------------------------------------------------------------------
# Static Frontend Mount
# -----------------------------------------------------------------------------
STATIC_DIR = config.PROJECT_ROOT / "web" / "static"
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

