"""Sprint 2 testleri: SCRUM-35, SCRUM-36, SCRUM-37."""
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.identity import User
from tests.conftest import auth_headers


def test_saglik_db(client: TestClient):
    resp = client.get("/api/saglik/db")
    assert resp.status_code == 200
    assert resp.json() == {"durum": "ok", "veritabani": "bagli"}


def test_author_register_login_and_me(client: TestClient, db_session: Session):
    resp = client.post(
        "/api/auth/register",
        json={
            "email": "Yazar@Test.COM",
            "password": "GucluSifre123!",
            "first_name": "Mehmet",
            "last_name": "Yazar",
            "role": "author",
            "privacy_notice_accepted": True,
            "pilot_consent_accepted": True,
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["email"] == "yazar@test.com"
    assert data["author_code"].startswith("YAZ-")
    assert data["pilot_consent_at"] is not None

    # Şifrenin düz metin değil Argon2 özetiyle saklandığını doğrula (BFR-02)
    db_user = db_session.scalar(select(User).where(User.email == "yazar@test.com"))
    assert db_user is not None
    assert db_user.password_hash.startswith("$argon2")

    # Giriş ve /me
    headers = auth_headers(client, "yazar@test.com", "GucluSifre123!")
    me_resp = client.get("/api/auth/me", headers=headers)
    assert me_resp.status_code == 200
    assert me_resp.json()["author_code"] == data["author_code"]


def test_duplicate_email_and_missing_kvkk_rejected(client: TestClient):
    # KVKK onaysız kayıt reddedilmeli
    resp_no_kvkk = client.post(
        "/api/auth/register",
        json={
            "email": "kvkk@test.com",
            "password": "GucluSifre123!",
            "first_name": "Ali",
            "last_name": "Veli",
            "role": "licensee",
            "privacy_notice_accepted": False,
        },
    )
    assert resp_no_kvkk.status_code == 422

    # Aynı e-posta ikinci kez kaydedilememeli
    payload = {
        "email": "alici@test.com",
        "password": "GucluSifre123!",
        "first_name": "Ali",
        "last_name": "Alıcı",
        "role": "licensee",
        "privacy_notice_accepted": True,
    }
    assert client.post("/api/auth/register", json=payload).status_code == 201
    assert client.post("/api/auth/register", json=payload).status_code == 409


def test_login_rate_limit(client: TestClient):
    client.post(
        "/api/auth/register",
        json={
            "email": "limit@test.com",
            "password": "DogruSifre123!",
            "first_name": "Deneme",
            "last_name": "Kullanıcı",
            "role": "licensee",
            "privacy_notice_accepted": True,
        },
    )
    for _ in range(5):
        r = client.post("/api/auth/login", json={"email": "limit@test.com", "password": "yanlis"})
        assert r.status_code == 401

    # 6. deneme 429 dönmeli (BFR-03)
    r6 = client.post("/api/auth/login", json={"email": "limit@test.com", "password": "DogruSifre123!"})
    assert r6.status_code == 429


def test_publisher_register_and_admin_approval_flow(
    client: TestClient, admin_user: User, db_session: Session
):
    reg = client.post(
        "/api/auth/register",
        json={
            "email": "yetkili@yayin.com",
            "password": "YayinSifre123!",
            "first_name": "Deniz",
            "last_name": "Yetkili",
            "role": "publisher_staff",
            "privacy_notice_accepted": True,
            "organization": {
                "name": "Örnek Yayıncılık A.Ş.",
                "tax_or_mersis_no": "9988776655",
                "corporate_email": "kurumsal@yayin.com",
            },
        },
    )
    assert reg.status_code == 201, reg.text
    org_id = reg.json()["organization"]["id"]
    assert reg.json()["organization"]["approval_status"] == "pending"

    # Yazar admin ucuna erişememeli (RBAC — BFR-04)
    pub_headers = auth_headers(client, "yetkili@yayin.com", "YayinSifre123!")
    assert client.get("/api/admin/publishers", headers=pub_headers).status_code == 403

    # Admin listeleyip onaylar (BFR-05)
    admin_headers = auth_headers(client, admin_user.email, "AdminSifre123!")
    pending_list = client.get(
        "/api/admin/publishers?approval_status=pending", headers=admin_headers
    )
    assert pending_list.status_code == 200
    assert any(item["id"] == org_id for item in pending_list.json())

    # Gerekçesiz ret 422 dönmeli
    bad_reject = client.post(
        f"/api/admin/publishers/{org_id}/review",
        headers=admin_headers,
        json={"decision": "rejected"},
    )
    assert bad_reject.status_code == 422

    # Onay işlemi başarılı olmalı ve audit_log'a yazılmalı
    approve_resp = client.post(
        f"/api/admin/publishers/{org_id}/review",
        headers=admin_headers,
        json={"decision": "approved"},
    )
    assert approve_resp.status_code == 200
    assert approve_resp.json()["approval_status"] == "approved"

    logs = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "publisher.approved")
    ).all()
    assert len(logs) == 1