from app.security import hash_password, needs_rehash, verify_password


def test_hash_is_argon2id_and_never_plaintext():
    h = hash_password("Gizli!Sifre1")
    assert h.startswith("$argon2id$") and "Gizli" not in h
    assert len(h) < 255                      # users.password_hash String(255) icine sigar


def test_same_password_gives_different_hashes():
    assert hash_password("ayni") != hash_password("ayni")   # rastgele tuz


def test_verify():
    h = hash_password("dogru-sifre")
    assert verify_password(h, "dogru-sifre")
    assert not verify_password(h, "yanlis-sifre")
    assert not verify_password("bozuk-ozet", "dogru-sifre")   # hata firlatmaz, False doner
    assert not needs_rehash(h)
