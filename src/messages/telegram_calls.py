from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from typing import Any

from telethon import TelegramClient, functions, types, utils


async def place_short_call(
    client: TelegramClient,
    recipient_username: str,
    duration_seconds: float = 1.0,
) -> None:
    username = recipient_username.strip().removeprefix("@").strip()
    recipient = await client.get_entity(username)
    if not isinstance(recipient, types.User) or recipient.bot or recipient.deleted:
        raise ValueError("call recipient must be an active Telegram user")
    if recipient.is_self:
        raise ValueError("call recipient cannot be the caller")
    if (recipient.username or "").casefold() != username.casefold():
        raise ValueError("resolved Telegram user does not match the configured username")

    dh = await client(functions.messages.GetDhConfigRequest(version=0, random_length=256))
    if not isinstance(dh, types.messages.DhConfig):
        raise RuntimeError("Telegram did not return fresh call key configuration")
    prime = int.from_bytes(dh.p, "big")
    exponent = secrets.randbelow(prime - 3) + 2
    key_material = pow(dh.g, exponent, prime).to_bytes((prime.bit_length() + 7) // 8, "big")
    protocol = types.PhoneCallProtocol(
        min_layer=65,
        max_layer=92,
        library_versions=["2.7.7"],
        udp_p2p=True,
        udp_reflector=True,
    )
    started_at = time.monotonic()
    result: Any = await client(
        functions.phone.RequestCallRequest(
            user_id=utils.get_input_user(recipient),
            random_id=secrets.randbits(31),
            g_a_hash=hashlib.sha256(key_material).digest(),
            protocol=protocol,
        )
    )
    phone_call = getattr(result, "phone_call", None)
    if not phone_call or not hasattr(phone_call, "access_hash"):
        raise RuntimeError("Telegram returned an unexpected call state")

    try:
        remaining = duration_seconds - (time.monotonic() - started_at)
        if remaining > 0:
            await asyncio.sleep(remaining)
    finally:
        await client(
            functions.phone.DiscardCallRequest(
                peer=types.InputPhoneCall(
                    id=phone_call.id,
                    access_hash=phone_call.access_hash,
                ),
                duration=max(0, round(time.monotonic() - started_at)),
                reason=types.PhoneCallDiscardReasonHangup(),
                connection_id=0,
            )
        )
