"""Generate the safe Google Colab migration notebook."""

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
    """Create a source-only bundle for upload to Colab."""
    include = [
        ROOT / "main.py",
        ROOT / "requirements.txt",
        *sorted((ROOT / "src").glob("*.py")),
        ROOT / "scripts" / "generate_session.py",
        ROOT / "scripts" / "status.py",
    ]
    BUNDLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        BUNDLE_PATH,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as bundle:
        for path in include:
            bundle.write(path, path.relative_to(ROOT))


def build_notebook() -> dict:
    return {
        "nbformat": 4,
        "nbformat_minor": 0,
        "metadata": {
            "colab": {"provenance": []},
            "kernelspec": {"name": "python3", "display_name": "Python 3"},
            "language_info": {"name": "python"},
        },
        "cells": [
            markdown(
                "# Google Photos to Telegram Migration\n\n"
                "This notebook runs the existing migration CLI on Google Colab. "
                "It does not open an SSH tunnel, embed credentials, or change the "
                "migration code.\n"
            ),
            markdown(
                "## 1. Mount Google Drive and download the project source\n\n"
                "The source is downloaded directly from GitHub, avoiding the "
                "browser upload widget."
            ),
            code(
                """from google.colab import drive
from pathlib import Path
import subprocess
import sys

drive.mount('/content/drive')
project_dir = Path('/content/Migration')
if project_dir.exists():
    subprocess.run(['rm', '-rf', str(project_dir)], check=True)
subprocess.run([
    'git', 'clone', '--depth', '1',
    'https://github.com/kimutaieddy/google-photos-telegram-migration.git',
    str(project_dir),
], check=True)
subprocess.run([
    sys.executable, '-m', 'pip', 'install', '-q', '-r',
    str(project_dir / 'requirements.txt'),
], check=True)
print(f'Project ready at {project_dir}')
"""
            ),
            markdown(
                "## 2. Configure Telegram and find Takeout archives\n\n"
                "The database and Telegram session are stored in Drive so a "
                "Colab restart can resume. Temporary extraction remains on the "
                "Colab VM."
            ),
            code(
                """import os
from getpass import getpass
from pathlib import Path
from telethon import TelegramClient
from telethon.sessions import StringSession

drive_data_dir = Path('/content/drive/MyDrive/Migration_Data')
drive_data_dir.mkdir(parents=True, exist_ok=True)
os.environ['DATA_DIR'] = str(drive_data_dir)
os.environ['TEMP_DIR'] = '/content/migration-temp'
os.environ['TELEGRAM_API_ID'] = input('Telegram API ID: ').strip()
os.environ['TELEGRAM_API_HASH'] = getpass('Telegram API hash: ').strip()
os.environ['TELEGRAM_CHANNEL_ID'] = input(
    'Telegram channel ID or @username: '
).strip()

session_file = drive_data_dir / 'telegram_session.txt'
if session_file.exists():
    os.environ['TELEGRAM_SESSION_STRING'] = session_file.read_text().strip()
else:
    print('Telegram session is a sensitive bearer credential. '
          'Anyone who can read this Drive file can access the Telegram account.')
    async def authorize_telegram():
        client = TelegramClient(
            StringSession(),
            int(os.environ['TELEGRAM_API_ID']),
            os.environ['TELEGRAM_API_HASH'],
        )
        await client.start()
        temporary_session_file = session_file.with_suffix('.tmp')
        temporary_session_file.write_text(client.session.save())
        temporary_session_file.replace(session_file)
        await client.disconnect()

    await authorize_telegram()
    os.environ['TELEGRAM_SESSION_STRING'] = session_file.read_text().strip()

default_takeout = Path('/content/drive/MyDrive/Takeout')
zip_files = [
    path for path in Path('/content/drive/MyDrive').rglob('*')
    if path.is_file() and path.suffix.lower() == '.zip'
]
if not default_takeout.is_dir() or not list(default_takeout.glob('*.zip')):
    candidates = zip_files
    default_takeout = candidates[0].parent if candidates else default_takeout
takeout_text = input(f'Takeout folder [{default_takeout}]: ').strip()
takeout_dir = Path(takeout_text or default_takeout)
archives = [
    path for path in takeout_dir.iterdir()
    if path.is_file() and path.suffix.lower() == '.zip'
] if takeout_dir.is_dir() else []
if not archives:
    raise FileNotFoundError(f'No Takeout ZIP archives found in {takeout_dir}')
print(f'Using {takeout_dir} ({len(archives)} archive(s))')
"""
            ),
            markdown(
                "## 3. Start or resume the migration\n\n"
                "This calls the existing `main.py` CLI. Re-running the cell "
                "resumes from the SQLite catalog."
            ),
            code(
                """import subprocess
import sys

subprocess.run([
    sys.executable, str(project_dir / 'main.py'),
    '--dir', str(takeout_dir),
    '--workers', '4',
], check=True)
"""
            ),
            markdown("## 4. Check progress"),
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
