"""
One-time Google Drive OAuth setup: authenticates and creates data/gdrive_token.json.
After running this once, the system connects to Google Drive automatically on future runs.
"""

import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.gdrive import GoogleDriveManager


def main():
    print("=" * 65)
    print(" 🔐 GOOGLE DRIVE ONE-TIME OAUTH AUTHENTICATION")
    print("=" * 65)
    print("\nThis script will open a browser window for you to log into Google")
    print("and grant read-only access to download your Takeout archives.")
    print("The access token will be saved permanently in 'data/gdrive_token.json'")
    print("with automatic background refresh.\n")

    manager = GoogleDriveManager()
    try:
        manager.authenticate(interactive=True)
        print("\n✅ Google Drive connected successfully!")
        print("Listing available Takeout archives in your Google Drive:\n")
        archives = manager.list_takeout_archives()
        if archives:
            print(f"Found {len(archives)} archive(s):")
            for a in archives[:10]:
                print(f"  - {a['name']} ({a['size_bytes'] / (1024*1024):.1f} MB)")
            if len(archives) > 10:
                print(f"  ... and {len(archives) - 10} more archives.")
        else:
            print("No archives found yet in 'Takeout' folder.")
    except Exception as e:
        print(f"\n❌ Authentication Error: {e}")
        print("\nEnsure you have placed your 'credentials.json' from Google Cloud Console into 'data/credentials.json'.")


if __name__ == "__main__":
    main()

