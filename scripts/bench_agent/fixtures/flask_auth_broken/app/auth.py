"""Minimal auth helpers."""
import hashlib

_USERS = {"alice": "5f4dcc3b5aa765d61d8327deb882cf99"}  # md5("password")


def hash_password(raw: str) -> str:
    return hashlib.md5(raw.encode()).hexdigest()


def check_password(user: str, raw: str) -> bool:
    stored = _USERS[user]
    return stored == hash_password(raw)


def login(user: str, raw: str) -> dict:
    if check_password(user, raw):
        return {"ok": True, "user": user}
    return {"ok": False}
