"""seed/books_sample.csv ve seed.py'nin CSV okuyucusu icin testler (veritabani gerektirmez)."""
from pathlib import Path

from app.isbn import is_valid_isbn13
from scripts.seed import SEED_DIR, read_catalog_csv

SAMPLE = SEED_DIR / "books_sample.csv"


def test_sample_catalog_is_valid_and_large_enough():
    rows, errors = read_catalog_csv(SAMPLE)
    assert errors == []
    assert 50 <= len(rows) <= 100          # paket kapsami: 50-100 kitap
    assert len({r.isbn for r in rows}) == len(rows)
    assert all(is_valid_isbn13(r.isbn) for r in rows)
    assert all(r.title and r.authors and r.publisher and r.year for r in rows)


def test_pilot_template_has_header_only_and_is_valid():
    rows, errors = read_catalog_csv(SEED_DIR / "books_pilot.csv")
    assert errors == [] and rows == []


def test_reader_reports_line_numbers(tmp_path: Path):
    f = tmp_path / "x.csv"
    f.write_text("isbn,title\n9780306406157,Iyi\n9780306406158,Kotu\n,\n", encoding="utf-8")
    rows, errors = read_catalog_csv(f)
    assert len(rows) == 1
    assert len(errors) == 1 and "satir 3" in errors[0]


def test_reader_handles_excel_semicolon_and_cp1254(tmp_path: Path):
    f = tmp_path / "excel.csv"
    f.write_bytes("isbn;title;year\n0-306-40615-2;Çöğüşı;2020\n".encode("cp1254"))
    rows, errors = read_catalog_csv(f)
    assert errors == []
    assert rows[0].isbn == "9780306406157" and rows[0].title == "Çöğüşı" and rows[0].year == 2020


def test_reader_rejects_missing_required_columns(tmp_path: Path):
    f = tmp_path / "x.csv"
    f.write_text("isbn,authors\n9780306406157,Ali\n", encoding="utf-8")
    _, errors = read_catalog_csv(f)
    assert errors and "title" in errors[0]
