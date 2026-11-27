"""ISBN-10/13 doğrulama, Türkçe metin normalizasyonu ve hibrit künye çözümleme servisi."""
from dataclasses import dataclass
import logging
import re
from typing import Any
import unicodedata

import httpx
from sqlalchemy.orm import Session

from app.config import ayarlar
from app.models.catalog import IsbnCatalog
from app.models.enums import CatalogSource, WorkMetadataSource

logger = logging.getLogger("eserlisans.isbn")

_TR_FOLD_MAP = str.maketrans(
    {
        "ç": "c",
        "ğ": "g",
        "ı": "i",
        "ö": "o",
        "ş": "s",
        "ü": "u",
        "â": "a",
        "î": "i",
        "û": "u",
    }
)


def normalize_turkish(text: str | None) -> str:
    """Türkçe büyük/küçük harf (İ/ı) ve aksanları normalize ederek arama anahtarı üretir."""
    if not text:
        return ""
    lowered = text.replace("İ", "i").replace("I", "ı").lower()
    decomposed = unicodedata.normalize("NFKD", lowered)
    without_combining = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return without_combining.translate(_TR_FOLD_MAP).strip()


def _isbn13_check_digit(first_12: str) -> str:
    total = sum(int(ch) * (1 if idx % 2 == 0 else 3) for idx, ch in enumerate(first_12))
    return str((10 - (total % 10)) % 10)


def validate_and_normalize_isbn(raw_isbn: str) -> str:
    """ISBN-10 veya ISBN-13 doğrular; veritabanı standardı olan 13 haneli ISBN döndürür."""
    cleaned = re.sub(r"[-\s]+", "", raw_isbn.strip()).upper()

    if len(cleaned) == 10:
        if not re.fullmatch(r"[0-9]{9}[0-9X]", cleaned):
            raise ValueError("Geçersiz ISBN-10 biçimi.")
        total = 0
        for idx, ch in enumerate(cleaned):
            val = 10 if ch == "X" else int(ch)
            total += (10 - idx) * val
        if total % 11 != 0:
            raise ValueError("ISBN-10 sağlama basamağı hatalı.")
        prefix_12 = f"978{cleaned[:9]}"
        return f"{prefix_12}{_isbn13_check_digit(prefix_12)}"

    if len(cleaned) == 13:
        if not cleaned.isdigit() or not cleaned.startswith(("978", "979")):
            raise ValueError("Geçersiz ISBN-13 biçimi (978 veya 979 ile başlamalıdır).")
        if _isbn13_check_digit(cleaned[:12]) != cleaned[12]:
            raise ValueError("ISBN-13 sağlama basamağı hatalı.")
        return cleaned

    raise ValueError("ISBN 10 veya 13 haneli olmalıdır.")


@dataclass
class IsbnMetadataResult:
    isbn: str
    found: bool
    metadata_source: WorkMetadataSource | None = None
    title: str | None = None
    authors_text: str | None = None
    publisher_name: str | None = None
    publication_year: int | None = None
    language_code: str = "tr"
    category: str | None = None


def _extract_year(date_text: str | None) -> int | None:
    if not date_text:
        return None
    match = re.search(r"(1[5-9]\d{2}|20\d{2})", str(date_text))
    return int(match.group(1)) if match else None


def _fetch_google_books(isbn: str) -> dict[str, Any] | None:
    url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
    try:
        resp = httpx.get(url, timeout=4.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Google Books erisilemedi (isbn=%s): %s", isbn, exc)
        return None

    items = data.get("items") or []
    if not items:
        return None
    info = items[0].get("volumeInfo") or {}
    title = info.get("title")
    if not title:
        return None
    authors = info.get("authors") or []
    categories = info.get("categories") or []
    return {
        "title": str(title)[:300],
        "authors_text": ", ".join(str(a) for a in authors)[:500] if authors else None,
        "publisher_name": str(info["publisher"])[:200] if info.get("publisher") else None,
        "publication_year": _extract_year(info.get("publishedDate")),
        "language_code": str(info.get("language") or "tr")[:10],
        "category": str(categories[0])[:100] if categories else None,
        "raw_payload": info,
    }


def _fetch_open_library(isbn: str) -> dict[str, Any] | None:
    url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"
    try:
        resp = httpx.get(url, timeout=4.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Open Library erisilemedi (isbn=%s): %s", isbn, exc)
        return None

    book = data.get(f"ISBN:{isbn}")
    if not book or not book.get("title"):
        return None
    authors = [a.get("name") for a in (book.get("authors") or []) if a.get("name")]
    publishers = [p.get("name") for p in (book.get("publishers") or []) if p.get("name")]
    subjects = [s.get("name") for s in (book.get("subjects") or []) if s.get("name")]
    return {
        "title": str(book["title"])[:300],
        "authors_text": ", ".join(authors)[:500] if authors else None,
        "publisher_name": publishers[0][:200] if publishers else None,
        "publication_year": _extract_year(book.get("publish_date")),
        "language_code": "tr",
        "category": subjects[0][:100] if subjects else None,
        "raw_payload": book,
    }


def lookup_isbn_hybrid(db: Session, raw_isbn: str) -> IsbnMetadataResult:
    """Hibrit ISBN çözümleme: Yerel katalog -> Google Books -> Open Library -> elle giriş."""
    isbn = validate_and_normalize_isbn(raw_isbn)

    cached = db.get(IsbnCatalog, isbn)
    if cached is not None:
        return IsbnMetadataResult(
            isbn=isbn,
            found=True,
            metadata_source=WorkMetadataSource.LOCAL_CATALOG,
            title=cached.title,
            authors_text=cached.authors_text,
            publisher_name=cached.publisher_name,
            publication_year=cached.publication_year,
            language_code=cached.language_code,
            category=cached.category,
        )

    if ayarlar().dis_isbn_servisleri:
        for fetcher, catalog_src, work_src in (
            (_fetch_google_books, CatalogSource.GOOGLE_BOOKS, WorkMetadataSource.GOOGLE_BOOKS),
            (_fetch_open_library, CatalogSource.OPEN_LIBRARY, WorkMetadataSource.OPEN_LIBRARY),
        ):
            fetched = fetcher(isbn)
            if fetched is not None:
                entry = IsbnCatalog(
                    isbn=isbn,
                    title=fetched["title"],
                    authors_text=fetched["authors_text"],
                    publisher_name=fetched["publisher_name"],
                    publication_year=fetched["publication_year"],
                    language_code=fetched["language_code"],
                    category=fetched["category"],
                    source=catalog_src,
                    raw_payload=fetched["raw_payload"],
                )
                db.add(entry)
                db.commit()
                return IsbnMetadataResult(
                    isbn=isbn,
                    found=True,
                    metadata_source=work_src,
                    title=entry.title,
                    authors_text=entry.authors_text,
                    publisher_name=entry.publisher_name,
                    publication_year=entry.publication_year,
                    language_code=entry.language_code,
                    category=entry.category,
                )

    return IsbnMetadataResult(isbn=isbn, found=False)