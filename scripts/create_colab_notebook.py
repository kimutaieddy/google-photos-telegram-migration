"""Generate the resumable, one-archive Google Colab runner."""

import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
NOTEBOOK_PATH = ROOT / "Google_Colab_Migration.ipynb"
BUNDLE_PATH = ROOT / "data" / "colab_bundle.zip"


def code(source: str) -> dict:
    return {
        "cell_type": "code",
        "metadata": {},
        "execution_count": None,
        "outputs": [],
        "source": source.splitlines(keepends=True),
    }


def markdown(source: str) -> dict:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": source.splitlines(keepends=True),
    }


def build_bundle() -> None:
    include = [
        ROOT / "main.py",
        ROOT / "requirements.txt",
        *sorted((ROOT / "src").glob("*.py")),
        ROOT / "scripts" / "status.py",
    ]
    BUNDLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BUNDLE_PATH, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in include:
            bundle.write(path, path.relative_to(ROOT))


def build_notebook() -> dict:
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "colab": {"provenance": []},
            "kernelspec": {"name": "python3", "display_name": "Python 3"},
            "language_info": {"name": "python"},
        },
        "cells": [
            markdown(
                "# Google Photos to Telegram: free resumable runner\n\n"
                "Run this notebook once per archive. It stores migration state in "
                "Google Drive, copies only the selected ZIP to temporary Colab disk, "
                "uploads through the existing CLI, and deletes only the temporary copy. "
                "The original ZIP in Google Drive is never deleted.\n\n"
                "**Before the first run:** put the stopped-run `migration.db` artifact "
                "in `MyDrive/Migration_Data/migration.db`. On later runs, leave it there."
            ),
            code(
                """from google.colab import drive
from pathlib import Path
import os
import shutil
import subprocess
import sys

drive.mount('/content/drive')
project_dir = Path('/content/google-photos-telegram-migration')
if project_dir.exists():
    shutil.rmtree(project_dir)
subprocess.run([
    'git', 'clone', '--depth', '1',
    'https://github.com/kimutaieddy/google-photos-telegram-migration.git',
    str(project_dir),
], check=True)
subprocess.run([
    sys.executable, '-m', 'pip', 'install', '-q', '-r',
    str(project_dir / 'requirements.txt'),
], check=True)
print('Project ready:', project_dir)
"""
            ),
            markdown(
                "## Configure persistent state and credentials\n\n"
                "The first run asks for Telegram credentials and saves only the "
                "session string in your private Google Drive. Do not paste secrets "
                "into notebook output or commit them to GitHub."
            ),
            code(
                """from getpass import getpass
from pathlib import Path
import os

drive_data_dir = Path('/content/drive/MyDrive/Migration_Data')
drive_data_dir.mkdir(parents=True, exist_ok=True)
os.environ['DATA_DIR'] = str(drive_data_dir)
os.environ['TEMP_DIR'] = '/content/migration-temp'
Path(os.environ['TEMP_DIR']).mkdir(parents=True, exist_ok=True)

def secret(name, prompt):
    value = os.environ.get(name) or getpass(prompt)
    if not value:
        raise ValueError(f'{name} is required')
    os.environ[name] = value

secret('TELEGRAM_API_ID', 'Telegram API ID: ')
secret('TELEGRAM_API_HASH', 'Telegram API hash: ')
secret('TELEGRAM_CHANNEL_ID', 'Telegram channel ID or @username: ')

session_path = drive_data_dir / 'telegram_session.txt'
if session_path.exists():
    os.environ['TELEGRAM_SESSION_STRING'] = session_path.read_text(encoding='utf-8').strip()
else:
    session = getpass('Telegram session string (saved privately in Drive): ')
    if not session:
        raise ValueError('TELEGRAM_SESSION_STRING is required')
    session_path.write_text(session.strip(), encoding='utf-8')
    os.environ['TELEGRAM_SESSION_STRING'] = session.strip()

print('Persistent state directory:', drive_data_dir)
print('Database present:', (drive_data_dir / 'migration.db').exists())
"""
            ),
            markdown(
                "## Select one archive\n\n"
                "The default is the archive left incomplete by the GitHub run. "
                "Change `archive_name` for the next 2 GB Takeout chunk."
            ),
            code(
                """from pathlib import Path

takeout_dir = Path('/content/drive/MyDrive/Takeout')
archive_name = 'takeout-20261008T145955Z-1-006.zip'
archive_path = takeout_dir / archive_name
if not archive_path.exists():
    raise FileNotFoundError(f'Archive not found: {archive_path}')
print('Selected archive:', archive_path.name)
print('Size GB:', round(archive_path.stat().st_size / 1024**3, 2))
"""
            ),
            markdown(
                "## Copy, migrate, and delete only the temporary copy\n\n"
                "This uses the existing directory mode. `--delete-after-upload` "
                "deletes the copied ZIP from Colab after processing; it cannot "
                "delete the source file mounted from Google Drive."
            ),
            code(
                """import shutil
import subprocess
import sys
from pathlib import Path

temp_dir = Path(os.environ['TEMP_DIR'])
local_archive = temp_dir / archive_path.name
try:
    shutil.copy2(archive_path, local_archive)
    subprocess.run([
        sys.executable, str(project_dir / 'main.py'),
        '--dir', str(temp_dir),
        '--delete-after-upload',
        '--workers', '2',
        '--max-gb', '5',
    ], check=True)
finally:
    if local_archive.exists():
        local_archive.unlink()
        print('Deleted temporary Colab copy:', local_archive.name)
"""
            ),
            markdown("## Check progress"),
            code(
                """subprocess.run([
    sys.executable, str(project_dir / 'scripts' / 'status.py'),
], check=True)
"""
            ),
        ],
    }


def main() -> None:
    build_bundle()
    NOTEBOOK_PATH.write_text(
        json.dumps(build_notebook(), indent=2),
        encoding="utf-8",
    )
    print(f"Created {BUNDLE_PATH}")
    print(f"Generated {NOTEBOOK_PATH}")


if __name__ == "__main__":
    main()
