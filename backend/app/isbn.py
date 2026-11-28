"""ISBN dogrulama ve normallestirme. Seed betigi VE API (eser kaydi) ayni fonksiyonlari kullanir.

Veritabaninda ISBN her zaman 13 hane, tiresiz, rakamdir (works.isbn / isbn_catalog.isbn).
ISBN-10 gelirse ISBN-13'e cevrilir.
"""
import re

_SEPARATORS = re.compile(r"[\s\-]")


class InvalidIsbnError(ValueError):
    """Gecersiz ISBN (bicim ya da kontrol hanesi hatali)."""


def clean_isbn(raw: str) -> str:
    """Tire ve bosluklari atar, harfleri buyuk yapar: '978-605-12-3456-7' -> '9786051234567'."""
    return _SEPARATORS.sub("", raw).upper()


def isbn13_check_digit(first12: str) -> str:
    """Ilk 12 haneden 13. (kontrol) haneyi hesaplar. Agirliklar sirayla 1, 3, 1, 3, ..."""
    total = sum(int(digit) * (1 if i % 2 == 0 else 3) for i, digit in enumerate(first12))
    return str((10 - total % 10) % 10)


def is_valid_isbn13(isbn: str) -> bool:
    return bool(re.fullmatch(r"97[89]\d{10}", isbn)) and isbn[-1] == isbn13_check_digit(isbn[:12])


def is_valid_isbn10(isbn: str) -> bool:
    """10. hane 'X' olabilir (deger 10). Agirlikli toplam 11'e tam bolunmeli."""
    if not re.fullmatch(r"\d{9}[\dX]", isbn):
        return False
    values = [10 if c == "X" else int(c) for c in isbn]
    return sum(weight * v for weight, v in zip(range(10, 0, -1), values)) % 11 == 0


def isbn10_to_isbn13(isbn10: str) -> str:
    """'0306406152' -> '9780306406157' (978 on eki + ilk 9 hane + yeni kontrol hanesi)."""
    base = "978" + isbn10[:9]
    return base + isbn13_check_digit(base)


def normalize_isbn(raw: str) -> str:
    """Kullanicidan/CSV'den gelen ISBN'i dogrular ve 13 haneli tiresiz bicime cevirir.

    Gecersizse InvalidIsbnError firlatir.
    """
    isbn = clean_isbn(raw or "")
    if len(isbn) == 13:
        if is_valid_isbn13(isbn):
            return isbn
        raise InvalidIsbnError(f"ISBN-13 gecersiz (bicim ya da kontrol hanesi hatali): {raw!r}")
    if len(isbn) == 10:
        if is_valid_isbn10(isbn):
            return isbn10_to_isbn13(isbn)
        raise InvalidIsbnError(f"ISBN-10 gecersiz (bicim ya da kontrol hanesi hatali): {raw!r}")
    raise InvalidIsbnError(f"ISBN 10 veya 13 haneli olmali: {raw!r}")
