import pytest

from app.isbn import (
    InvalidIsbnError, clean_isbn, is_valid_isbn10, is_valid_isbn13, isbn10_to_isbn13,
    isbn13_check_digit, normalize_isbn,
)


def test_check_digit_known_example():
    # Bilinen ornek: 978-0-306-40615-7
    assert isbn13_check_digit("978030640615") == "7"


@pytest.mark.parametrize("isbn", ["9780306406157", "9789750719387", "9790100000017"])
def test_valid_isbn13(isbn):
    assert is_valid_isbn13(isbn)


@pytest.mark.parametrize("isbn", [
    "9780306406158",   # kontrol hanesi yanlis
    "9770306406157",   # 978/979 ile baslamiyor
    "978030640615",    # 12 hane
    "97803064061570",  # 14 hane
    "978030640615X",   # harf
    "",
])
def test_invalid_isbn13(isbn):
    assert not is_valid_isbn13(isbn)


def test_isbn10_including_x_check_digit():
    assert is_valid_isbn10("0306406152")
    assert is_valid_isbn10("080442957X")   # kontrol hanesi X (=10)
    assert not is_valid_isbn10("0306406153")


def test_isbn10_to_isbn13():
    assert isbn10_to_isbn13("0306406152") == "9780306406157"
    assert normalize_isbn("080442957X") == "9780804429573"


def test_clean_removes_hyphens_and_spaces():
    assert clean_isbn(" 978-0-306 40615-7 ") == "9780306406157"
    assert clean_isbn("080442957x") == "080442957X"


@pytest.mark.parametrize("raw", ["978-0-306-40615-7", "9780306406157", "0-306-40615-2", " 0306406152 "])
def test_normalize_valid_inputs_give_same_isbn13(raw):
    assert normalize_isbn(raw) == "9780306406157"


@pytest.mark.parametrize("raw", ["", "123", "9780306406158", "0306406153", "abcdefghijklm", None])
def test_normalize_invalid_raises(raw):
    with pytest.raises(InvalidIsbnError):
        normalize_isbn(raw)


def test_every_valid_isbn13_passes_database_format_check():
    # Veritabani CHECK'i: ^[0-9]{13}$
    import re
    assert re.fullmatch(r"[0-9]{13}", normalize_isbn("0-306-40615-2"))
