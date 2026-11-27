"""Ortak pytest fixture'ları.

- TEST_DATABASE_URL ortam değişkeni verilirse o PostgreSQL veritabanını kullanır.
- Verilmezse (ör. GitHub Actions CI) in-memory SQLite ile tüm şemayı ayağa kaldırır.
"""
from collections.abc import Iterator
from datetime import datetime, timezone
import os
import re

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import BigInteger, create_engine, event
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateIndex, CreateTable

import app.models  # noqa: F401 — tüm modellerin Base.metadata'ya kaydı için
from app.core.security import hash_password, login_rate_limiter
from app.database import Base, get_db
from app.main import app as fastapi_app
from app.models.enums import ApprovalStatus, OrganizationType, UserRole
from app.models.identity import Organization, User


# --- PostgreSQL tiplerinin, kısıtlarının ve kısmi indekslerinin SQLite uyarlaması ---
@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


@compiles(INET, "sqlite")
def _compile_inet_sqlite(type_, compiler, **kw):
    return "VARCHAR(45)"


@compiles(BigInteger, "sqlite")
def _compile_bigint_sqlite(type_, compiler, **kw):
    return "INTEGER"


@compiles(CreateTable, "sqlite")
def _compile_create_table_sqlite(element, compiler, **kw):
    ddl = compiler.visit_create_table(element, **kw)
    ddl = re.sub(r"isbn\s*~\s*'\^\[0-9\]\{13\}\$'", "length(isbn) = 13", ddl)
    ddl = ddl.replace("char_length(token_hash) = 64", "length(token_hash) = 64")
    return ddl


@compiles(CreateIndex, "sqlite")
def _compile_create_index_sqlite(element, compiler, **kw):
    ddl = compiler.visit_create_index(element, **kw)
    pg_where = element.element.dialect_options["postgresql"].get("where")
    if pg_where is not None and " WHERE " not in ddl.upper():
        where_sql = compiler.sql_compiler.process(pg_where, literal_binds=True)
        ddl = f"{ddl} WHERE {where_sql}"
    return ddl


@pytest.fixture()
def db_session() -> Iterator[Session]:
    test_db_url = os.getenv("TEST_DATABASE_URL")
    if test_db_url:
        engine = create_engine(test_db_url, pool_pre_ping=True)
    else:
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        if not test_db_url:
            Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def client(db_session: Session) -> Iterator[TestClient]:
    login_rate_limiter.clear_all()

    def _override_get_db() -> Iterator[Session]:
        yield db_session

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    with TestClient(fastapi_app) as test_client:
        yield test_client
    fastapi_app.dependency_overrides.clear()


@pytest.fixture()
def admin_user(db_session: Session) -> User:
    user = User(
        email="admin@eserlisans.com",
        password_hash=hash_password("AdminSifre123!"),
        first_name="Sistem",
        last_name="Yöneticisi",
        role=UserRole.ADMIN,
        is_active=True,
        privacy_notice_accepted_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def auth_headers(client: TestClient, email: str, password: str) -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def approved_publisher_user(db_session: Session) -> User:
    now = datetime.now(timezone.utc)
    org = Organization(
        type=OrganizationType.PUBLISHER,
        name="Gazi Yayınevi",
        tax_or_mersis_no="1234567890",
        corporate_email="info@gaziyayinevi.com",
        approval_status=ApprovalStatus.APPROVED,
        reviewed_at=now,
    )
    db_session.add(org)
    db_session.flush()
    user = User(
        email="yayinevi@eserlisans.com",
        password_hash=hash_password("Yayinevi123!"),
        first_name="Ayşe",
        last_name="Yayıncı",
        role=UserRole.PUBLISHER_STAFF,
        organization_id=org.id,
        is_active=True,
        privacy_notice_accepted_at=now,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user