"""Sifre ozetleme (Argon2id). Duz metin sifre hicbir yerde saklanmaz.

Seed betigi ve API (kayit/giris) ayni fonksiyonlari kullanir. Mervenur JWT islerini
bu dosyaya (veya ayri bir dosyaya) ekleyebilir.
"""
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # argon2-cffi'nin guvenli varsayilan parametreleri (Argon2id)


def hash_password(password: str) -> str:
    """Her cagrida farkli rastgele tuz kullanir; ayni sifre farkli ozet uretir. Cikti ~97 karakter."""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Ileride Argon2 parametreleri guclendirilirse, giris yapan kullanicinin ozeti yenilenir."""
    return _hasher.check_needs_rehash(password_hash)
