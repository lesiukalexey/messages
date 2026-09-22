from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from telethon import TelegramClient, events

from .config import Settings
from .store import Store


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


async def run() -> None:
    load_dotenv()
    settings = Settings.from_environment()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
    logger = logging.getLogger(__name__)

    settings.session_path.parent.mkdir(parents=True, exist_ok=True)
    store = Store(settings.database_path)
    store.initialize()
    client = TelegramClient(str(settings.session_path), settings.api_id, settings.api_hash)

    @client.on(events.NewMessage(incoming=True))
    async def on_message(event: events.NewMessage.Event) -> None:
        peer_id = event.chat_id
        mode = store.contact_mode(peer_id)
        if mode == "off":
            store.audit(peer_id, "skipped", "contact policy is off")
            return

        # Reply generation and sending are intentionally not implemented yet.
        store.audit(peer_id, "skipped", f"mode {mode} is not enabled in the initial scaffold")
        logger.info("Skipped incoming message from peer %s under mode %s", peer_id, mode)

    try:
        await client.start()
        logger.info("Telegram assistant is running in draft-first scaffold mode")
        await client.run_until_disconnected()
    finally:
        store.close()
        await client.disconnect()


def main() -> None:
    asyncio.run(run())

