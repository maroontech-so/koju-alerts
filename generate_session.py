"""Run locally once to create a Telethon StringSession for your own Telegram account."""
import asyncio
import os

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

load_dotenv()


async def main():
    api_id = int(os.getenv("API_ID") or "0")
    api_hash = os.getenv("API_HASH", "")
    if api_id <= 0 or not api_hash:
        raise SystemExit("Set API_ID and API_HASH in your local .env first.")
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.start()
    print("\nSESSION_STRING (keep private; paste into Render secret SESSION_STRING):\n")
    print(client.session.save())
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())