"""
Generate a Telethon StringSession for headless / Docker operation.
Run this locally or once on VPS to obtain TELEGRAM_SESSION_STRING.
"""

import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telethon import TelegramClient
from telethon.sessions import StringSession
from src.config import config


async def main():
    print("=" * 60)
    print(" Telegram StringSession Generator")
    print("=" * 60)

    api_id = config.TELEGRAM_API_ID
    api_hash = config.TELEGRAM_API_HASH

    if not api_id or not api_hash:
        api_id_input = input("Enter your Telegram API ID (from https://my.telegram.org): ").strip()
        api_id = int(api_id_input)
        api_hash = input("Enter your Telegram API HASH: ").strip()

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.start()

    session_string = client.session.save()
    me = await client.get_me()

    print("\n" + "=" * 60)
    print(f" Authorization Successful for: {me.first_name} (@{me.username})")
    print("=" * 60)
    print("\nAdd the following line to your .env file:\n")
    print(f"TELEGRAM_SESSION_STRING={session_string}\n")
    print("=" * 60)

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

