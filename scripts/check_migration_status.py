import json
import os
from pathlib import Path
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import sqlite3

# Paths
BASE_DIR = Path(__file__).resolve().parents[1]
TOKEN_PATH = BASE_DIR / 'data' / 'gdrive_token.json'
CLIENT_SECRET_PATH = BASE_DIR / 'client_secret_272953376689-1p3f658rsu9qmir1v3ivhdag6nmtnjs7.apps.googleusercontent.com.json'
DB_PATH = BASE_DIR / 'data' / 'migration.db'

# Load credentials
with open(TOKEN_PATH, 'r') as f:
    token_data = json.load(f)
creds = Credentials(
    token=token_data.get('token'),
    refresh_token=token_data.get('refresh_token'),
    token_uri=token_data.get('token_uri'),
    client_id=token_data.get('client_id'),
    client_secret=token_data.get('client_secret'),
    scopes=token_data.get('scopes')
)
# Refresh if needed
if creds.expired or not creds.valid:
    creds.refresh(Request())
    # Save refreshed token back
    with open(TOKEN_PATH, 'w') as f:
        f.write(creds.to_json())
    print('✅ Token refreshed and saved')
else:
    print('✅ Token already valid')

# Build Drive service
service = build('drive', 'v3', credentials=creds)
# List all Takeout zip files (name contains 'takeout-' and ends with .zip)
query = "name contains 'takeout-' and name contains '.zip' and trashed = false"
files = []
page_token = None
while True:
    resp = service.files().list(q=query, spaces='drive', fields='nextPageToken, files(id, name, size)', pageToken=page_token, pageSize=1000).execute()
    files.extend(resp.get('files', []))
    page_token = resp.get('nextPageToken')
    if not page_token:
        break
print(f'📁 Drive: found {len(files)} Takeout zip archives')

# Load DB archives
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute('SELECT id, archive_name, status, size_bytes FROM archives')
rows = cur.fetchall()
print(f'🗂️ DB: {len(rows)} archives recorded')
# Summarize statuses
status_counts = {}
for r in rows:
    status_counts[r['status']] = status_counts.get(r['status'], 0) + 1
print('Status distribution in DB:', status_counts)

# Find any Drive archives not present or not marked COMPLETED in DB
drive_names = {f['name'] for f in files}
completed_names = {r['archive_name'] for r in rows if r['status'] == 'COMPLETED'}
missing_in_db = drive_names - set(r['archive_name'] for r in rows)
not_completed = drive_names - completed_names
print('❓ Archives on Drive missing from DB:', missing_in_db)
print('❗ Archives on Drive not marked COMPLETED:', not_completed)

# Check if any media items still pending
cur.execute('SELECT COUNT(*) FROM media_items WHERE status NOT IN ("UPLOADED", "VERIFIED")')
pending_items = cur.fetchone()[0]
print(f'⏳ Pending media items in DB: {pending_items}')

if not_missing := (len(missing_in_db) == 0) and (len(not_completed) == 0) and pending_items == 0:
    print('✅ Migration appears COMPLETE – all Takeout archives processed and all media uploaded.')
else:
    print('⚠️ Migration NOT complete. See above details.')

