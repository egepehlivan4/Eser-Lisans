from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.database import OturumFabrikasi
from app.models import (
    AuditLog,
    LicenseRequest,
    LicenseTerms,
    Organization,
    Payment,
    RevenueShare,
    User,
    Work,
)
from app.models.enums import (
    ApprovalStatus,
    IntendedUse,
    OrganizationType,
    PaymentProvider,
    PaymentStatus,
    PermissionLevel,
    RecipientType,
    RequestStatus,
    TermsStatus,
    UserRole,
    WorkMetadataSource,
)

NOW = datetime.now(timezone.utc)


def expect_error(session, label, exc_type, action):
    try:
        with session.begin_nested():
            action()
            session.flush()
    except exc_type as exc:
        print(f"  OK   {label}  ->  {type(exc).__name__}")
    else:
        raise SystemExit(f"HATA: '{label}' engellenmedi!")


def main() -> None:
    session = OturumFabrikasi()
    try:
        pub_org = Organization(
            type=OrganizationType.PUBLISHER,
            name="Guard Test Yayinevi",
            tax_or_mersis_no="9999999991",
            corporate_email="guard@yayinevi.example",
            approval_status=ApprovalStatus.APPROVED,
        )
        pub_user = User(
            email="guard_pub@example.org",
            password_hash="x" * 20,
            first_name="Ali",
            last_name="Test",
            role=UserRole.PUBLISHER_STAFF,
            organization=pub_org,
            privacy_notice_accepted_at=NOW,
        )
        author = User(
            email="guard_author@example.org",
            password_hash="x" * 20,
            first_name="Ayse",
            last_name="Test",
            role=UserRole.AUTHOR,
            author_code="YZR-999991",
            privacy_notice_accepted_at=NOW,
        )
        licensee = User(
            email="guard_licensee@example.org",
            password_hash="x" * 20,
            first_name="Can",
            last_name="Test",
            role=UserRole.LICENSEE,
            privacy_notice_accepted_at=NOW,
        )
        work = Work(
            isbn="9789750718533",
            title="Turkce Yapay Zeka Kitabi",
            publisher=pub_org,
            metadata_source=WorkMetadataSource.MANUAL,
            registered_by=pub_user,
        )
        terms = LicenseTerms(
            work=work,
            version_no=1,
            permission_level=PermissionLevel.ACADEMIC_ONLY,
            fee_amount=Decimal("10000.00"),
            author_share_pct=Decimal("60"),
            publisher_share_pct=Decimal("40"),
            status=TermsStatus.APPROVED,
            proposed_by=pub_user,
        )
        req = LicenseRequest(
            work=work,
            terms=terms,
            licensee_user=licensee,
            intended_use=IntendedUse.ACADEMIC_NONCOMMERCIAL,
            purpose_description="Test",
            status=RequestStatus.AWAITING_PAYMENT,
        )
        req2 = LicenseRequest(
            work=work,
            terms=terms,
            licensee_user=licensee,
            intended_use=IntendedUse.ACADEMIC_NONCOMMERCIAL,
            purpose_description="Test 2",
            status=RequestStatus.REQUESTED,
        )
        pay = Payment(
            request=req,
            amount=Decimal("10000.00"),
            provider=PaymentProvider.INTERNAL_SIMULATION,
            status=PaymentStatus.SUCCEEDED,
            idempotency_key="guard-pay-1",
            completed_at=NOW,
        )
        pay.revenue_shares += [
            RevenueShare(
                recipient_type=RecipientType.PLATFORM,
                share_percent=Decimal("15"),
                amount=Decimal("1500.00"),
            ),
            RevenueShare(
                recipient_type=RecipientType.AUTHOR,
                recipient_user=author,
                share_percent=Decimal("51"),
                amount=Decimal("5100.00"),
            ),
            RevenueShare(
                recipient_type=RecipientType.PUBLISHER,
                recipient_org=pub_org,
                share_percent=Decimal("34"),
                amount=Decimal("3400.00"),
            ),
        ]
        log = AuditLog(
            actor=licensee,
            action="payment.succeeded",
            entity_type="payment",
            entity_id="1",
            ip_address="127.0.0.1",
        )
        session.add_all([req2, pay, log])
        session.flush()
        session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

        print("1) AuditLog ORM ve DB korumalari")
        expect_error(
            session,
            "ORM nesne UPDATE engeli",
            PermissionError,
            lambda: setattr(log, "action", "hacked"),
        )
        session.expire(log)

        expect_error(
            session,
            "ORM nesne DELETE engeli",
            PermissionError,
            lambda: session.delete(log),
        )
        session.expunge(log)

        expect_error(
            session,
            "ORM toplu UPDATE engeli",
            PermissionError,
            lambda: session.execute(
                update(AuditLog).where(AuditLog.id == log.id).values(action="hack")
            ),
        )
        expect_error(
            session,
            "ORM toplu DELETE engeli",
            PermissionError,
            lambda: session.execute(delete(AuditLog).where(AuditLog.id == log.id)),
        )
        expect_error(
            session,
            "Ham SQL audit_log UPDATE engeli",
            DBAPIError,
            lambda: session.execute(
                text("UPDATE audit_log SET action='hack' WHERE id=:id"), {"id": log.id}
            ),
        )
        expect_error(
            session,
            "Ham SQL audit_log DELETE engeli",
            DBAPIError,
            lambda: session.execute(
                text("DELETE FROM audit_log WHERE id=:id"), {"id": log.id}
            ),
        )

        print("2) Mali kayit ve durum gecis korumalari")
        expect_error(
            session,
            "revenue_shares UPDATE engeli",
            DBAPIError,
            lambda: session.execute(
                text("UPDATE revenue_shares SET amount=1 WHERE payment_id=:pid"),
                {"pid": pay.id},
            ),
        )
        expect_error(
            session,
            "license_terms bedel degistirme engeli",
            DBAPIError,
            lambda: session.execute(
                text("UPDATE license_terms SET fee_amount=1 WHERE id=:id"),
                {"id": terms.id},
            ),
        )
        expect_error(
            session,
            "license_terms gecersiz durum gecisi (approved -> proposed)",
            DBAPIError,
            lambda: session.execute(
                text("UPDATE license_terms SET status='proposed' WHERE id=:id"),
                {"id": terms.id},
            ),
        )
        expect_error(
            session,
            "license_requests gecersiz durum gecisi (requested -> active)",
            DBAPIError,
            lambda: session.execute(
                text("UPDATE license_requests SET status='active' WHERE id=:id"),
                {"id": req2.id},
            ),
        )
        expect_error(
            session,
            "sonuclanmis odeme (succeeded) degistirme engeli",
            DBAPIError,
            lambda: session.execute(
                text("UPDATE payments SET status='failed' WHERE id=:id"),
                {"id": pay.id},
            ),
        )
        expect_error(
            session,
            "payments DELETE engeli",
            DBAPIError,
            lambda: session.execute(
                text("DELETE FROM payments WHERE id=:id"), {"id": pay.id}
            ),
        )

        print("3) B7 finansal toplam kontrolu (pay toplami != odeme tutari)")

        def create_unbalanced_payment():
            bad_pay = Payment(
                request=req2,
                amount=Decimal("10000.00"),
                provider=PaymentProvider.INTERNAL_SIMULATION,
                status=PaymentStatus.SUCCEEDED,
                idempotency_key="guard-pay-bad",
                completed_at=NOW,
            )
            bad_pay.revenue_shares.append(
                RevenueShare(
                    recipient_type=RecipientType.PLATFORM,
                    share_percent=Decimal("15"),
                    amount=Decimal("1500.00"),
                )
            )
            session.add(bad_pay)
            session.flush()
            session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

        expect_error(
            session,
            "eksik gelir payi toplami (1500 != 10000) engeli",
            IntegrityError,
            create_unbalanced_payment,
        )

        print("4) pg_trgm Turkce arama testi")
        found = session.execute(
            text("SELECT title FROM works WHERE title ILIKE '%Yapay Zeka%'")
        ).scalar_one()
        assert "Yapay Zeka" in found
        print(f"  OK   pg_trgm arama sonucu: {found}")

        print("\nTUM ADIM 3 GUVENLIK KONTROLLERI BASARIYLA GECTI!")
    finally:
        session.rollback()
        session.close()


if __name__ == "__main__":
    main()