"""
Google Drive integration: automated discovery and sequential downloading of Takeout archives.
"""

import io
import logging
import os
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from src.config import config
from src.downloader import assert_disk_space_available

logger = logging.getLogger(__name__)

# Scopes required: read-only access to files
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


class GoogleDriveManager:
    """Manages persistent Google Drive OAuth connection and file downloads."""

    def __init__(
        self,
        token_path: Optional[Path] = None,
        credentials_path: Optional[Path] = None,
    ):
        self.token_path = token_path or (config.DATA_DIR / "gdrive_token.json")
        self.credentials_path = credentials_path or (config.DATA_DIR / "credentials.json")
        self.service = None

    def authenticate(self, interactive: bool = True) -> Any:
        """
        Authenticate with Google Drive using saved token or initial OAuth flow.
        Tokens are cached permanently in data/gdrive_token.json with auto-refresh.
        """
        creds = None
        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Refreshing expired Google Drive credentials...")
                creds.refresh(Request())
            elif interactive:
                if not self.credentials_path.exists():
                    raise FileNotFoundError(
                        f"Google OAuth client secrets file not found at: {self.credentials_path}\n"
                        "Please place your 'credentials.json' from Google Cloud Console into the 'data/' folder."
                    )
                logger.info("Initiating Google Drive OAuth flow in local browser...")
                flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_path), SCOPES)
                creds = flow.run_local_server(port=0)

                # Save token for future automatic execution
                self.token_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.token_path, "w") as token_file:
                    token_file.write(creds.to_json())
                logger.info(f"Saved persistent Google Drive token to {self.token_path}")
            else:
                raise PermissionError("Google Drive credentials not configured or expired.")

        self.service = build("drive", "v3", credentials=creds)
        logger.info("Google Drive service successfully initialized.")
        return self.service

    def get_service(self):
        if not self.service:
            self.authenticate()
        return self.service

    def list_takeout_archives(self, folder_name: str = "Takeout") -> List[Dict[str, Any]]:
        """
        Search for the specified folder in Google Drive and list all Takeout ZIP files inside it.
        """
        drive = self.get_service()

        # 1. Find folder ID
        query = f"mimeType = 'application/vnd.google-apps.folder' and name = '{folder_name}' and trashed = false"
        res = drive.files().list(q=query, fields="files(id, name)").execute()
        folders = res.get("files", [])

        if not folders:
            logger.warning(f"No folder named '{folder_name}' found in Google Drive root.")
            # Fallback: search for any takeout-*.zip files in the entire drive
            file_query = "name contains 'takeout-' and name contains '.zip' and trashed = false"
        else:
            folder_id = folders[0]["id"]
            logger.info(f"Found '{folder_name}' folder (ID: {folder_id})")
            file_query = f"'{folder_id}' in parents and name contains '.zip' and trashed = false"

        # 2. List all ZIP archives
        items: List[Dict[str, Any]] = []
        page_token = None

        while True:
            file_res = drive.files().list(
                q=file_query,
                spaces="drive",
                fields="nextPageToken, files(id, name, size, md5Checksum, createdTime)",
                pageToken=page_token,
                pageSize=100,
            ).execute()

            for f in file_res.get("files", []):
                items.append({
                    "id": f["id"],
                    "name": f["name"],
                    "size_bytes": int(f.get("size", 0)),
                    "md5": f.get("md5Checksum"),
                    "created_at": f.get("createdTime"),
                })

            page_token = file_res.get("nextPageToken")
            if not page_token:
                break

        # Sort archives naturally by name (e.g. 001.zip, 002.zip)
        items.sort(key=lambda x: x["name"])
        logger.info(f"Discovered {len(items)} Takeout archive(s) in Google Drive.")
        return items

    def download_archive(
        self,
        file_id: str,
        destination_path: Path,
        expected_size_mb: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Path:
        """
        Download a Takeout archive from Google Drive chunk by chunk with disk headroom enforcement.
        """
        drive = self.get_service()
        destination_path.parent.mkdir(parents=True, exist_ok=True)

        req_mb = expected_size_mb or 2200
        assert_disk_space_available(destination_path.parent, required_mb=req_mb)

        request = drive.files().get_media(fileId=file_id)

        chunk_size = 10 * 1024 * 1024  # 10 MB download chunk
        with open(destination_path, "wb") as f_out:
            downloader = MediaIoBaseDownload(f_out, request, chunksize=chunk_size)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status:
                    curr = int(status.resumable_progress)
                    total = int(status.total_size)
                    if progress_callback:
                        progress_callback(curr, total)

        logger.info(f"Successfully downloaded Drive file to {destination_path.name}")
        return destination_path
