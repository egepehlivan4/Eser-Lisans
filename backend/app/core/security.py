"""Kimlik doğrulama yardımcıları: Argon2, JWT, giriş hız sınırı ve Yazar Kodu."""
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
import secrets
import time
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
import jwt

from app.config import ayarlar

_password_hasher = PasswordHasher()


def hash_password(plain_password: str) -> str:
    return _password_hasher.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, plain_password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_access_token(user_id: int, role: str) -> tuple[str, int]:
    """Kısa ömürlü JWT erişim belirteci ve geçerlilik süresini (saniye) döndürür."""
    cfg = ayarlar()
    now = datetime.now(timezone.utc)
    expires_delta = timedelta(minutes=cfg.jwt_sure_dakika)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    token = jwt.encode(payload, cfg.jwt_gizli_anahtar, algorithm=cfg.jwt_algoritma)
    return token, int(expires_delta.total_seconds())


def decode_access_token(token: str) -> dict[str, Any]:
    cfg = ayarlar()
    return jwt.decode(token, cfg.jwt_gizli_anahtar, algorithms=[cfg.jwt_algoritma])


def generate_author_code() -> str:
    """Örnek: YAZ-A1B2C3D4"""
    return f"YAZ-{secrets.token_hex(4).upper()}"


class LoginRateLimiter:
    """Belirli zaman penceresinde başarısız giriş denemelerini sınırlar (BFR-03)."""

    def __init__(self, max_attempts: int = 5, window_seconds: int = 300) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> deque[float]:
        bucket = self._attempts[key]
        cutoff = now - self.window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        return bucket

    def is_limited(self, key: str) -> bool:
        now = time.monotonic()
        bucket = self._prune(key, now)
        return len(bucket) >= self.max_attempts

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        bucket = self._prune(key, now)
        bucket.append(now)

    def reset(self, key: str) -> None:
        self._attempts.pop(key, None)

    def clear_all(self) -> None:
        self._attempts.clear()


login_rate_limiter = LoginRateLimiter(max_attempts=5, window_seconds=300)