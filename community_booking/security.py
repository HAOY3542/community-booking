import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import OrderedDict

from fastapi import HTTPException


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(value: str) -> str:
    salt = secrets.token_bytes(16)
    hashed = hashlib.scrypt(value.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${hashed.hex()}"


def verify_password(value: str, encoded: str) -> bool:
    try:
        algorithm, salt, expected = encoded.split("$")
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(value.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def ticket_for(key: bytes, booking_id: str, version: int, expires: int) -> str:
    data = json.dumps([booking_id, version, expires], separators=(",", ":")).encode()
    payload = base64.urlsafe_b64encode(data).decode().rstrip("=")
    signature = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    return payload + "." + signature


def read_ticket(key: bytes, ticket: str, now: int):
    try:
        payload, signature = ticket.split(".")
        expected = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if (not isinstance(data, list) or len(data) != 3 or not isinstance(data[0], str)
                or type(data[1]) is not int or type(data[2]) is not int or now >= data[2]):
            raise ValueError()
        return data
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(404, "凭证无效或已过期") from None


class RateLimiter:
    """Bounded, single-process protection for the local demo, not a distributed limiter."""
    def __init__(self):
        self.entries = OrderedDict()
        self.lock = threading.Lock()

    def check(self, key: str, limit: int, window: int):
        now = time.monotonic()
        with self.lock:
            values = [v for v in self.entries.pop(key, []) if now - v < window]
            if len(values) >= limit:
                self.entries[key] = values
                raise HTTPException(429, "操作频繁，请稍后再试")
            values.append(now)
            self.entries[key] = values
            while len(self.entries) > 4096:
                self.entries.popitem(last=False)
