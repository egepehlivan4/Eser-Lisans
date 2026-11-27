"""Sprint 5-7 testleri: SCRUM-60 (kullanım raporu), SCRUM-69 (CSV/audit), SCRUM-81 (OpenAPI)."""
import csv
from datetime import date, timedelta
import io

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.identity import User
from tests.conftest import auth_headers
from tests.test_revenue_and_licensing import _setup_approved_work


def _activate_paid_license(
    client: TestClient,
    db_session: Session,
    approved_publisher_user: User,
    monkeypatch: pytest.MonkeyPatch,
    *,
    isbn: str = "9789750719387",
) -> tuple[int, int, dict[str, str], dict[str, str], dict[str, str]]:
    work_id, pub_h, auth_h, lic_h = _setup_approved_work(
        client,
        db_session,
        approved_publisher_user,
        monkeypatch,
        isbn=isbn,
        permission_level="commercial_included",
        fee_amount="10000.00",
    )
    req = client.post(
        "/api/license-requests",
        headers=lic_h,
        json={
            "work_id": work_id,
            "intended_use": "commercial",
            "purpose_description": "Türkçe dil modeli ön eğitimi için lisans talebi.",
        },
    )
    req_id = req.json()["id"]
    client.post(f"/api/license-requests/{req_id}/decision", headers=auth_h, json={"decision": "approved"})
    client.post(f"/api/license-requests/{req_id}/decision", headers=pub_h, json={"decision": "approved"})
    client.post(f"/api/license-requests/{req_id}/accept-contract", headers=lic_h, json={"accepted": True})
    pay = client.post(
        f"/api/license-requests/{req_id}/pay",
        headers=lic_h,
        json={
            "provider": "internal_simulation",
            "idempotency_key": f"pay-report-{isbn}",
            "simulation_scenario": "success",
        },
    )
    assert pay.status_code == 200
    license_id = pay.json()["license"]["id"]
    return req_id, license_id, pub_h, auth_h, lic_h


def test_usage_report_lifecycle_and_validation(
    client: TestClient,
    db_session: Session,
    approved_publisher_user: User,
    admin_user: User,
    monkeypatch: pytest.MonkeyPatch,
):
    _, license_id, pub_h, auth_h, lic_h = _activate_paid_license(
        client, db_session, approved_publisher_user, monkeypatch
    )

    # Beyan onaysız (False) veya ters tarih aralığı 422 dönmeli
    bad_decl = client.post(
        f"/api/licenses/{license_id}/usage-reports",
        headers=lic_h,
        json={
            "usage_type": "pretraining",
            "period_start": "2026-11-01",
            "period_end": "2026-11-25",
            "model_name": "GaziLLM-v1",
            "description": "İlk Türkçe derlem ön eğitim denemesi.",
            "declaration_accepted": False,
        },
    )
    assert bad_decl.status_code == 422

    bad_dates = client.post(
        f"/api/licenses/{license_id}/usage-reports",
        headers=lic_h,
        json={
            "usage_type": "pretraining",
            "period_start": "2026-11-25",
            "period_end": "2026-11-01",
            "description": "Hatalı tarih aralığı kontrolü.",
            "declaration_accepted": True,
        },
    )
    assert bad_dates.status_code == 422

    # Aktif lisans için geçerli kullanım raporu (SCRUM-60 / BFR-27)
    ok = client.post(
        f"/api/licenses/{license_id}/usage-reports",
        headers=lic_h,
        json={
            "usage_type": "fine_tuning",
            "period_start": "2026-11-01",
            "period_end": "2026-11-27",
            "model_name": "GaziLLM-v1",
            "description": "Akademik ince ayar aşamasında izinli metin olarak kullanıldı.",
            "declaration_accepted": True,
        },
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["usage_type"] == "fine_tuning"

    # Lisans alıcısı, yazar ve yayınevi raporu görebilmeli
    for headers in (lic_h, auth_h, pub_h):
        lst = client.get(f"/api/licenses/{license_id}/usage-reports", headers=headers)
        assert lst.status_code == 200
        assert len(lst.json()) == 1

    # Lisans süresi dolduktan sonra yeni kullanım raporu 409 dönmeli (BFR-27)
    admin_h = auth_headers(client, admin_user.email, "AdminSifre123!")
    future = date.today() + timedelta(days=400)
    client.post(f"/api/licenses/expire-overdue?as_of={future}", headers=admin_h)

    after_expire = client.post(
        f"/api/licenses/{license_id}/usage-reports",
        headers=lic_h,
        json={
            "usage_type": "evaluation",
            "period_start": "2026-11-01",
            "period_end": "2026-11-27",
            "description": "Süresi dolmuş lisansta rapor engellenmeli.",
            "declaration_accepted": True,
        },
    )
    assert after_expire.status_code == 409


def test_license_report_csv_export_and_audit_logs(
    client: TestClient,
    db_session: Session,
    approved_publisher_user: User,
    admin_user: User,
    monkeypatch: pytest.MonkeyPatch,
):
    _, license_id, pub_h, auth_h, lic_h = _activate_paid_license(
        client, db_session, approved_publisher_user, monkeypatch, isbn="9780132350884"
    )
    client.post(
        f"/api/licenses/{license_id}/usage-reports",
        headers=lic_h,
        json={
            "usage_type": "pretraining",
            "period_start": "2026-11-10",
            "period_end": "2026-11-20",
            "model_name": "TurkLM-7B",
            "description": "Ön eğitim kümesine eklendi.",
            "declaration_accepted": True,
        },
    )

    # Lisans alıcısı gelir/lisans dışa aktarma raporuna erişemez (UC17: Yazar, Yayınevi)
    assert client.get("/api/reports/licenses/export", headers=lic_h).status_code == 403

    # Yazar CSV dışa aktarımı (SCRUM-69 / BFR-30)
    author_csv_resp = client.get("/api/reports/licenses/export", headers=auth_h)
    assert author_csv_resp.status_code == 200
    assert "text/csv" in author_csv_resp.headers["content-type"]
    text_body = author_csv_resp.content.decode("utf-8-sig")
    parsed_author = list(csv.DictReader(io.StringIO(text_body)))
    assert len(parsed_author) == 1
    assert parsed_author[0]["my_revenue_share"] == "5100.00"
    assert parsed_author[0]["platform_commission"] == "1500.00"
    assert parsed_author[0]["usage_report_count"] == "1"

    # Yayınevi JSON ve CSV raporu
    pub_json = client.get("/api/reports/licenses", headers=pub_h)
    assert pub_json.status_code == 200
    assert pub_json.json()[0]["my_revenue_share"] == "3400.00"

    # UC16 / BFR-29: Sistem Yöneticisi tüm denetim kayıtlarını, yazar sadece kendininkini görür
    admin_h = auth_headers(client, admin_user.email, "AdminSifre123!")
    admin_logs = client.get("/api/audit-logs", headers=admin_h).json()
    author_logs = client.get("/api/audit-logs", headers=auth_h).json()
    assert len(admin_logs) > len(author_logs)
    assert len(author_logs) > 0
    author_ids_in_log = {row["actor_user_id"] for row in author_logs}
    assert len(author_ids_in_log) == 1

    # BFR-28: Denetim günlüğü model katmanında değiştirilemez ve silinemez
    first_log = db_session.scalar(select(AuditLog).limit(1))
    assert first_log is not None
    first_log.action = "tampered"
    with pytest.raises(PermissionError):
        db_session.commit()
    db_session.rollback()

    db_session.delete(first_log)
    with pytest.raises(PermissionError):
        db_session.commit()
    db_session.rollback()


def test_openapi_documentation_endpoints(client: TestClient):
    """SCRUM-81: Swagger UI ve OpenAPI şeması tüm uç noktalarla hatasız üretilmeli."""
    docs_resp = client.get("/docs")
    assert docs_resp.status_code == 200

    schema_resp = client.get("/openapi.json")
    assert schema_resp.status_code == 200
    paths = schema_resp.json()["paths"]
    for required_path in (
        "/api/auth/register",
        "/api/auth/login",
        "/api/admin/publishers/{org_id}/review",
        "/api/works",
        "/api/works/isbn/{isbn}",
        "/api/license-requests",
        "/api/license-requests/{request_id}/pay",
        "/api/revenue-shares",
        "/api/licenses/{license_id}/usage-reports",
        "/api/audit-logs",
        "/api/reports/licenses/export",
    ):
        assert required_path in paths