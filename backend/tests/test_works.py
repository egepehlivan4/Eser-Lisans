"""Sprint 3 testleri: SCRUM-44, SCRUM-46, SCRUM-45, SCRUM-85."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import generate_author_code, hash_password
from app.models.catalog import IsbnCatalog
from app.models.enums import CatalogSource, InvitationStatus, UserRole
from app.models.identity import Invitation, User
from app.services import isbn_service
from app.services.isbn_service import normalize_turkish, validate_and_normalize_isbn
from tests.conftest import auth_headers


def _create_author(db: Session, email: str, first_name: str, last_name: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password("YazarSifre123!"),
        first_name=first_name,
        last_name=last_name,
        role=UserRole.AUTHOR,
        author_code=generate_author_code(),
        is_active=True,
        privacy_notice_accepted_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _create_licensee(db: Session, email: str = "alici@nlp.com") -> User:
    user = User(
        email=email,
        password_hash=hash_password("AliciSifre123!"),
        first_name="Can",
        last_name="Araştırmacı",
        role=UserRole.LICENSEE,
        is_active=True,
        privacy_notice_accepted_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_isbn_validation_and_turkish_normalization():
    # Geçerli ISBN-13 ve ISBN-10 -> 13 dönüşümü
    assert validate_and_normalize_isbn("978-975-07-1938-7") == "9789750719387"
    assert validate_and_normalize_isbn("0-306-40615-2") == "9780306406157"

    with pytest.raises(ValueError):
        validate_and_normalize_isbn("9789750719380")  # yanlış sağlama basamağı
    with pytest.raises(ValueError):
        validate_and_normalize_isbn("12345")

    assert normalize_turkish("İSTANBUL'da Işık ve Şeker") == "istanbul'da isik ve seker"
    assert normalize_turkish("Yapay Zekâ Öğrenme") == "yapay zeka ogrenme"


def test_hybrid_isbn_lookup_cache_and_fallback(
    client: TestClient,
    db_session: Session,
    approved_publisher_user: User,
    monkeypatch: pytest.MonkeyPatch,
):
    pub_headers = auth_headers(client, approved_publisher_user.email, "Yayinevi123!")

    # 1) Yerel katalogda varsa doğrudan local_catalog döner
    db_session.add(
        IsbnCatalog(
            isbn="9789750719387",
            title="İnce Memed",
            authors_text="Yaşar Kemal",
            publisher_name="Yapı Kredi",
            publication_year=1955,
            source=CatalogSource.SEED,
        )
    )
    db_session.commit()

    r1 = client.get("/api/works/isbn/978-975-07-1938-7", headers=pub_headers)
    assert r1.status_code == 200
    assert r1.json()["found"] is True
    assert r1.json()["metadata_source"] == "local_catalog"
    assert r1.json()["title"] == "İnce Memed"

    # 2) Yerelde yoksa Google Books'tan çekip yerel kataloğa önbellekler
    monkeypatch.setattr(
        isbn_service,
        "_fetch_google_books",
        lambda isbn: {
            "title": "Türkçe Doğal Dil İşleme",
            "authors_text": "Örnek Yazar",
            "publisher_name": "Gazi Yayınları",
            "publication_year": 2025,
            "language_code": "tr",
            "category": "Bilgisayar",
            "raw_payload": {"mock": True},
        },
    )
    r2 = client.get("/api/works/isbn/9780306406157", headers=pub_headers)
    assert r2.status_code == 200
    assert r2.json()["metadata_source"] == "google_books"
    assert db_session.get(IsbnCatalog, "9780306406157") is not None

    # 3) Dış servisler başarısız olursa found=False döner (elle girişe düşer)
    monkeypatch.setattr(isbn_service, "_fetch_google_books", lambda isbn: None)
    monkeypatch.setattr(isbn_service, "_fetch_open_library", lambda isbn: None)
    r3 = client.get("/api/works/isbn/9780131103627", headers=pub_headers)
    assert r3.status_code == 200
    assert r3.json()["found"] is False


def test_maker_checker_approval_revision_unavailable_and_turkish_search(
    client: TestClient,
    db_session: Session,
    approved_publisher_user: User,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(isbn_service, "_fetch_google_books", lambda isbn: None)
    monkeypatch.setattr(isbn_service, "_fetch_open_library", lambda isbn: None)

    author = _create_author(db_session, "yazar1@gazi.com", "İsmail", "Şahin")
    licensee = _create_licensee(db_session)

    pub_headers = auth_headers(client, approved_publisher_user.email, "Yayinevi123!")
    author_headers = auth_headers(client, author.email, "YazarSifre123!")
    licensee_headers = auth_headers(client, licensee.email, "AliciSifre123!")

    # Yazar ID doğrulama (SCRUM-46)
    lookup = client.get(
        f"/api/works/authors/lookup?author_code={author.author_code}", headers=pub_headers
    )
    assert lookup.status_code == 200
    assert lookup.json()["id"] == author.id

    # Eser kaydı (SCRUM-44)
    create_resp = client.post(
        "/api/works",
        headers=pub_headers,
        json={
            "isbn": "978-0-13-110362-7",
            "title": "İleri Yapay Zekâ ve Öğrenme",
            "publication_year": 2026,
            "authors": [{"author_code": author.author_code, "split_percent": "100"}],
            "terms": {
                "permission_level": "academic_only",
                "fee_amount": "10000.00",
                "author_share_pct": "60",
                "publisher_share_pct": "40",
            },
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    work = create_resp.json()
    work_id = work["id"]
    terms_v1_id = work["current_terms"]["id"]
    assert work["status"] == "pending_approval"

    # Onaylanmadan lisans alıcısı göremez (BFR-12, BFR-14)
    assert client.get("/api/works", headers=licensee_headers).json() == []
    assert client.get(f"/api/works/{work_id}", headers=licensee_headers).status_code == 403

    # Yazar revizyon talep eder (SCRUM-45 / BFR-10)
    rev_resp = client.post(
        f"/api/works/{work_id}/terms/{terms_v1_id}/decision",
        headers=author_headers,
        json={"decision": "revision_requested", "comment": "Yazar payı %70 olsun."},
    )
    assert rev_resp.status_code == 200
    assert rev_resp.json()["current_terms"]["status"] == "revision_requested"

    # Yayınevi v2 teklifini gönderir (BFR-11)
    v2_resp = client.post(
        f"/api/works/{work_id}/terms",
        headers=pub_headers,
        json={
            "permission_level": "commercial_included",
            "fee_amount": "12000.00",
            "author_share_pct": "70",
            "publisher_share_pct": "30",
        },
    )
    assert v2_resp.status_code == 201
    terms_v2_id = v2_resp.json()["current_terms"]["id"]
    assert v2_resp.json()["current_terms"]["version_no"] == 2

    # Yazar v2'yi onaylar -> eser lisanslanabilir olur (BFR-12)
    ok_resp = client.post(
        f"/api/works/{work_id}/terms/{terms_v2_id}/decision",
        headers=author_headers,
        json={"decision": "approved"},
    )
    assert ok_resp.status_code == 200
    assert ok_resp.json()["status"] == "licensable"

    # Türkçe karakter normalizasyonu ile arama (SCRUM-85)
    search1 = client.get("/api/works?q=ileri yapay zeka", headers=licensee_headers)
    assert search1.status_code == 200
    assert len(search1.json()) == 1

    search2 = client.get("/api/works?author=ismail sahin", headers=licensee_headers)
    assert len(search2.json()) == 1

    search3 = client.get(
        "/api/works?permission_level=commercial_included", headers=licensee_headers
    )
    assert len(search3.json()) == 1

    # Yazar eseri tek tıkla "kullanılamaz" yapabilir (BFR-10)
    unavail = client.post(
        f"/api/works/{work_id}/mark-unavailable",
        headers=author_headers,
        json={"reason": "Eğitim iznini kapattım"},
    )
    assert unavail.status_code == 200
    assert unavail.json()["status"] == "unavailable"
    assert unavail.json()["current_terms"]["permission_level"] == "unavailable"
    assert client.get("/api/works", headers=licensee_headers).json() == []


def test_unregistered_author_invitation_and_registration_link(
    client: TestClient,
    db_session: Session,
    approved_publisher_user: User,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(isbn_service, "_fetch_google_books", lambda isbn: None)
    monkeypatch.setattr(isbn_service, "_fetch_open_library", lambda isbn: None)

    captured_tokens: list[str] = []
    original_send = isbn_service  # sadece dosya içi import doğrulaması
    assert original_send is not None

    from app.routers import works as works_router

    def _capture_email(email: str, work, raw_token: str) -> None:
        captured_tokens.append(raw_token)

    monkeypatch.setattr(works_router, "_send_invitation_email", _capture_email)

    pub_headers = auth_headers(client, approved_publisher_user.email, "Yayinevi123!")

    # Kayıtlı olmayan yazar e-postası ile eser kaydı (SCRUM-46)
    resp = client.post(
        "/api/works",
        headers=pub_headers,
        json={
            "isbn": "9780201633610",
            "title": "Tasarım Desenleri",
            "authors": [{"email": "yeni.yazar@gazi.com", "split_percent": "100"}],
            "terms": {
                "permission_level": "academic_only",
                "fee_amount": "5000.00",
                "author_share_pct": "60",
                "publisher_share_pct": "40",
            },
        },
    )
    assert resp.status_code == 201, resp.text
    assert len(captured_tokens) == 1
    work_id = resp.json()["id"]

    # Davet edilen yazar token ile kaydolduğunda esere otomatik bağlanır
    reg = client.post(
        "/api/auth/register",
        json={
            "email": "yeni.yazar@gazi.com",
            "password": "YeniYazar123!",
            "first_name": "Zeynep",
            "last_name": "Kaya",
            "role": "author",
            "privacy_notice_accepted": True,
            "invitation_token": captured_tokens[0],
        },
    )
    assert reg.status_code == 201, reg.text

    inv = db_session.scalar(select(Invitation).where(Invitation.work_id == work_id))
    assert inv is not None and inv.status == InvitationStatus.ACCEPTED

    # Artık yeni yazar eseri görüp onaylayabilir
    new_author_headers = auth_headers(client, "yeni.yazar@gazi.com", "YeniYazar123!")
    my_works = client.get("/api/works", headers=new_author_headers)
    assert len(my_works.json()) == 1
    terms_id = my_works.json()[0]["current_terms"]["id"]

    approve = client.post(
        f"/api/works/{work_id}/terms/{terms_id}/decision",
        headers=new_author_headers,
        json={"decision": "approved"},
    )
    assert approve.status_code == 200
    assert approve.json()["status"] == "licensable"