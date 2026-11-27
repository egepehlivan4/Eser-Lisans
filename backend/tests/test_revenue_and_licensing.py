"""Sprint 4 testleri: SCRUM-50, SCRUM-51, SCRUM-90, SCRUM-91 ve B7 (24 hesaplama senaryosu)."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from app.core.security import generate_author_code, hash_password
from app.models.enums import UserRole
from app.models.identity import User
from app.services import isbn_service
from app.services.revenue_service import calculate_revenue_shares
from tests.conftest import auth_headers

# B7 Ölçütü: En az 20 elle hesaplanmış senaryo (burada 22 tek yazarlı + 2 çoklu yazarlı = 24 senaryo)
B7_SINGLE_AUTHOR_SCENARIOS = [
    # (bedel, yazar_%, yayinevi_%, beklenen_platform, beklenen_yazar, beklenen_yayinevi)
    ("10000.00", "60", "40", "1500.00", "5100.00", "3400.00"),
    ("10000.00", "50", "50", "1500.00", "4250.00", "4250.00"),
    ("10000.00", "70", "30", "1500.00", "5950.00", "2550.00"),
    ("10000.00", "80", "20", "1500.00", "6800.00", "1700.00"),
    ("10000.00", "100", "0", "1500.00", "8500.00", "0.00"),
    ("10000.00", "0", "100", "1500.00", "0.00", "8500.00"),
    ("100.00", "60", "40", "15.00", "51.00", "34.00"),
    ("100.00", "50", "50", "15.00", "42.50", "42.50"),
    ("100.00", "70", "30", "15.00", "59.50", "25.50"),
    ("1.00", "50", "50", "0.15", "0.43", "0.42"),          # kuruş yuvarlama farkı yayınevine
    ("1.00", "60", "40", "0.15", "0.51", "0.34"),
    ("1.00", "70", "30", "0.15", "0.60", "0.25"),
    ("33.33", "60", "40", "5.00", "17.00", "11.33"),
    ("33.33", "50", "50", "5.00", "14.17", "14.16"),
    ("33.33", "70", "30", "5.00", "19.83", "8.50"),
    ("99.99", "60", "40", "15.00", "50.99", "34.00"),
    ("99.99", "50", "50", "15.00", "42.50", "42.49"),
    ("99.99", "70", "30", "15.00", "59.49", "25.50"),
    ("1234.56", "60", "40", "185.18", "629.63", "419.75"),
    ("1234.56", "70", "30", "185.18", "734.57", "314.81"),
    ("250000.00", "60", "40", "37500.00", "127500.00", "85000.00"),
    ("0.05", "50", "50", "0.01", "0.02", "0.02"),
]


@pytest.mark.parametrize(
    "total,author_pct,pub_pct,exp_platform,exp_author,exp_pub",
    B7_SINGLE_AUTHOR_SCENARIOS,
)
def test_b7_revenue_share_exact_calculations(
    total: str,
    author_pct: str,
    pub_pct: str,
    exp_platform: str,
    exp_author: str,
    exp_pub: str,
):
    res = calculate_revenue_shares(
        total_amount=Decimal(total),
        author_share_pct=Decimal(author_pct),
        publisher_share_pct=Decimal(pub_pct),
        author_splits=[(1, Decimal("100"))],
    )
    assert res.platform_amount == Decimal(exp_platform)
    assert res.author_allocations[0].amount == Decimal(exp_author)
    assert res.publisher_amount == Decimal(exp_pub)
    # Parçaların toplamı her zaman bedele kuruşu kuruşuna eşit olmalı
    assert res.platform_amount + res.publisher_amount + res.author_allocations[0].amount == Decimal(total)


def test_b7_multi_author_splits_and_validation():
    # Senaryo 23: 3 eşit yazar (%33.33 / %33.33 / %33.34)
    res1 = calculate_revenue_shares(
        total_amount=Decimal("1000.00"),
        author_share_pct=Decimal("60"),
        publisher_share_pct=Decimal("40"),
        author_splits=[(10, Decimal("33.33")), (11, Decimal("33.33")), (12, Decimal("33.34"))],
    )
    assert res1.platform_amount == Decimal("150.00")
    assert res1.publisher_amount == Decimal("340.00")
    assert sum(a.amount for a in res1.author_allocations) == Decimal("510.00")

    # Senaryo 24: 2 yazar (%70 / %30), tekil kuruşlu bedel
    res2 = calculate_revenue_shares(
        total_amount=Decimal("101.01"),
        author_share_pct=Decimal("70"),
        publisher_share_pct=Decimal("30"),
        author_splits=[(10, Decimal("70")), (11, Decimal("30"))],
    )
    total_distributed = (
        res2.platform_amount + res2.publisher_amount + sum(a.amount for a in res2.author_allocations)
    )
    assert total_distributed == Decimal("101.01")

    with pytest.raises(ValueError):
        calculate_revenue_shares(
            total_amount=Decimal("100.00"),
            author_share_pct=Decimal("60"),
            publisher_share_pct=Decimal("30"),
            author_splits=[(1, Decimal("100"))],
        )


def _setup_approved_work(
    client: TestClient,
    db_session: Session,
    approved_publisher_user: User,
    monkeypatch: pytest.MonkeyPatch,
    *,
    isbn: str,
    permission_level: str,
    fee_amount: str,
) -> tuple[int, dict[str, str], dict[str, str], dict[str, str]]:
    monkeypatch.setattr(isbn_service, "_fetch_google_books", lambda x: None)
    monkeypatch.setattr(isbn_service, "_fetch_open_library", lambda x: None)

    author = User(
        email=f"yazar.{isbn[-4:]}@gazi.com",
        password_hash=hash_password("Yazar123!"),
        first_name="Ayşe",
        last_name="Bilgin",
        role=UserRole.AUTHOR,
        author_code=generate_author_code(),
        is_active=True,
        privacy_notice_accepted_at=datetime.now(timezone.utc),
    )
    licensee = User(
        email=f"alici.{isbn[-4:]}@nlp.com",
        password_hash=hash_password("Alici123!"),
        first_name="Emre",
        last_name="Araştırmacı",
        role=UserRole.LICENSEE,
        is_active=True,
        privacy_notice_accepted_at=datetime.now(timezone.utc),
    )
    db_session.add_all([author, licensee])
    db_session.commit()

    pub_h = auth_headers(client, approved_publisher_user.email, "Yayinevi123!")
    auth_h = auth_headers(client, author.email, "Yazar123!")
    lic_h = auth_headers(client, licensee.email, "Alici123!")

    w = client.post(
        "/api/works",
        headers=pub_h,
        json={
            "isbn": isbn,
            "title": "Türkçe Büyük Dil Modelleri",
            "authors": [{"author_code": author.author_code, "split_percent": "100"}],
            "terms": {
                "permission_level": permission_level,
                "fee_amount": fee_amount,
                "duration_months": 12,
                "author_share_pct": "60",
                "publisher_share_pct": "40",
            },
        },
    )
    assert w.status_code == 201, w.text
    work_id = w.json()["id"]
    terms_id = w.json()["current_terms"]["id"]

    ok = client.post(
        f"/api/works/{work_id}/terms/{terms_id}/decision",
        headers=auth_h,
        json={"decision": "approved"},
    )
    assert ok.status_code == 200
    return work_id, pub_h, auth_h, lic_h


def test_paid_license_end_to_end_failures_idempotency_and_expiration(
    client: TestClient,
    db_session: Session,
    approved_publisher_user: User,
    admin_user: User,
    monkeypatch: pytest.MonkeyPatch,
):
    work_id, pub_h, auth_h, lic_h = _setup_approved_work(
        client,
        db_session,
        approved_publisher_user,
        monkeypatch,
        isbn="9780131103627",
        permission_level="academic_only",
        fee_amount="10000.00",
    )

    # BFR-15: academic_only esere ticari talep 422 dönmeli
    bad_use = client.post(
        "/api/license-requests",
        headers=lic_h,
        json={
            "work_id": work_id,
            "intended_use": "commercial",
            "purpose_description": "Ticari sohbet robotu eğitimi için kullanılacaktır.",
        },
    )
    assert bad_use.status_code == 422

    # Akademik talep oluşturulur -> awaiting_approval
    req_resp = client.post(
        "/api/license-requests",
        headers=lic_h,
        json={
            "work_id": work_id,
            "intended_use": "academic_noncommercial",
            "purpose_description": "Gazi Üniversitesi Türkçe NLP araştırması için kullanılacaktır.",
        },
    )
    assert req_resp.status_code == 201, req_resp.text
    req_id = req_resp.json()["id"]
    assert req_resp.json()["status"] == "awaiting_approval"

    # Yazar onaylar -> henüz yayınevi onaylamadığı için awaiting_approval kalır
    d1 = client.post(
        f"/api/license-requests/{req_id}/decision",
        headers=auth_h,
        json={"decision": "approved"},
    )
    assert d1.status_code == 200
    assert d1.json()["status"] == "awaiting_approval"

    # BFR-18: Aynı yazar tekrar onaylayamaz
    d1_dup = client.post(
        f"/api/license-requests/{req_id}/decision",
        headers=auth_h,
        json={"decision": "approved"},
    )
    assert d1_dup.status_code == 409

    # Yayınevi onaylar -> çift onay tamamlandı: approved
    d2 = client.post(
        f"/api/license-requests/{req_id}/decision",
        headers=pub_h,
        json={"decision": "approved"},
    )
    assert d2.status_code == 200
    assert d2.json()["status"] == "approved"

    # Sözleşme kabulü -> ücretli olduğu için awaiting_payment (SCRUM-51)
    acc = client.post(
        f"/api/license-requests/{req_id}/accept-contract",
        headers=lic_h,
        json={"accepted": True},
    )
    assert acc.status_code == 200
    assert acc.json()["status"] == "awaiting_payment"

    # BFR-22: Başarısız ödeme senaryoları (lisans aktif olmamalı)
    for scenario in ("insufficient_funds", "invalid_card", "suspected_fraud"):
        fail_resp = client.post(
            f"/api/license-requests/{req_id}/pay",
            headers=lic_h,
            json={
                "provider": "internal_simulation",
                "idempotency_key": f"key-{scenario}-0001",
                "simulation_scenario": scenario,
            },
        )
        assert fail_resp.status_code == 200
        body = fail_resp.json()
        assert body["payment"]["status"] == "failed"
        assert body["payment"]["failure_reason"] == scenario
        assert body["request_status"] == "awaiting_payment"
        assert body["license"] is None

    # Başarılı ödeme (iyzico_sandbox adaptörü ile — BFR-21, BFR-23)
    pay_ok = client.post(
        f"/api/license-requests/{req_id}/pay",
        headers=lic_h,
        json={
            "provider": "iyzico_sandbox",
            "idempotency_key": "key-success-0001",
            "simulation_scenario": "success",
        },
    )
    assert pay_ok.status_code == 200, pay_ok.text
    pay_data = pay_ok.json()
    assert pay_data["payment"]["status"] == "succeeded"
    assert pay_data["request_status"] == "active"
    assert pay_data["license"]["license_number"].startswith("EL-")
    assert pay_data["idempotent_replay"] is False

    # BFR-24: Aynı idempotency_key tekrar gelirse ikinci kez tahsilat yapılmaz
    replay = client.post(
        f"/api/license-requests/{req_id}/pay",
        headers=lic_h,
        json={
            "provider": "iyzico_sandbox",
            "idempotency_key": "key-success-0001",
            "simulation_scenario": "success",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["payment"]["id"] == pay_data["payment"]["id"]

    # UC13: Yazar (5100 TL) ve yayınevi (3400 TL) gelir paylarını görüntüler
    author_rev = client.get("/api/revenue-shares", headers=auth_h)
    assert author_rev.status_code == 200
    assert Decimal(author_rev.json()["total_amount"]) == Decimal("5100.00")

    pub_rev = client.get("/api/revenue-shares", headers=pub_h)
    assert pub_rev.status_code == 200
    assert Decimal(pub_rev.json()["total_amount"]) == Decimal("3400.00")

    # BFR-19: Bitiş tarihinden sonra lisans otomatik expired olur
    admin_h = auth_headers(client, admin_user.email, "AdminSifre123!")
    future_date = date.today() + timedelta(days=400)
    exp_resp = client.post(f"/api/licenses/expire-overdue?as_of={future_date}", headers=admin_h)
    assert exp_resp.status_code == 200
    assert exp_resp.json()["expired_count"] == 1

    detail = client.get(f"/api/license-requests/{req_id}", headers=lic_h)
    assert detail.json()["status"] == "expired"


def test_free_license_skips_payment_and_activates_directly(
    client: TestClient,
    db_session: Session,
    approved_publisher_user: User,
    monkeypatch: pytest.MonkeyPatch,
):
    work_id, pub_h, auth_h, lic_h = _setup_approved_work(
        client,
        db_session,
        approved_publisher_user,
        monkeypatch,
        isbn="9780201633610",
        permission_level="commercial_included",
        fee_amount="0.00",
    )

    req = client.post(
        "/api/license-requests",
        headers=lic_h,
        json={
            "work_id": work_id,
            "intended_use": "commercial",
            "purpose_description": "Açık kaynak Türkçe model eğitimi için ücretsiz lisans talebi.",
        },
    )
    req_id = req.json()["id"]

    client.post(f"/api/license-requests/{req_id}/decision", headers=auth_h, json={"decision": "approved"})
    client.post(f"/api/license-requests/{req_id}/decision", headers=pub_h, json={"decision": "approved"})

    # Ücretsiz lisans sözleşme kabulünde ödeme kaydı olmadan doğrudan active olur
    acc = client.post(
        f"/api/license-requests/{req_id}/accept-contract",
        headers=lic_h,
        json={"accepted": True},
    )
    assert acc.status_code == 200
    assert acc.json()["status"] == "active"
    assert acc.json()["license"] is not None
    assert acc.json()["payments"] == []