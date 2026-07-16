"""Parse @device commands from chat messages."""

from __future__ import annotations

import re
from dataclasses import dataclass


# name: letters, digits, _.- and CJK
_AT = re.compile(
    r"^\s*@(?P<name>[\w.\-一-鿿]+)\s+(?P<body>.+)$",
    re.DOTALL,
)


@dataclass
class DeviceCommand:
    device_name: str
    body: str


def parse_device_command(message: str) -> DeviceCommand | None:
    """Return DeviceCommand if message starts with @name ..."""
    if not message or "@" not in message:
        return None
    m = _AT.match(message.strip())
    if not m:
        return None
    name = m.group("name").strip()
    body = m.group("body").strip()
    if not name or not body:
        return None
    return DeviceCommand(device_name=name, body=body)
