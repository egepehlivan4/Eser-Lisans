from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.database import OturumFabrikasi
from app.models import (
    AuditLog,
    Invitation,
    License,
    LicenseRequest,
    LicenseTerms,
    Organization,
    Payment,
    RequestApproval,
    RevenueShare,
    TermsApproval,
    UsageReport,
    User,
    Work,
    WorkAuthor,
)
from app.models.enums import (
    AiUsageType,
    ApprovalDecision,
    ApprovalParty,
    ApprovalStatus,
    IntendedUse,
    OrganizationType,
    PaymentFailureReason,
    PaymentProvider,
    PaymentStatus,
    PermissionLevel,
    RecipientType,
    RequestDecision,
    RequestStatus,
    TermsStatus,
    UserRole,
    WorkMetadataSource,
    WorkStatus,
)

NOW = datetime.now(timezone.utc)


def new_user(email, role, org=None, author_code=None, first="Test"):
    return User(
        email=email,
        password_hash="x" * 20,
        first_name=first,
        last_name="Kullanici",
        role=role,
        organization=org,
        author_code=author_code,
        privacy_notice_accepted_at=NOW,
    )


def expect_violation(session, label, build):
    try:
        with session.begin_nested():
            build()
            session.flush()
    except IntegrityError as exc:
        diag = getattr(exc.orig, "diag", None)
        cname = getattr(diag, "constraint_name", str(exc.orig))
        print(f"  OK   {label}  ->  {cname}")
    else:
        raise SystemExit(f"HATA: '{label}' veritabani tarafindan ENGELLENMEDI!")


def main() -> None:
    session = OturumFabrikasi()
    try:
        print("1) Normal akis testi")
        pub_org = Organization(
            type=OrganizationType.PUBLISHER,
            name="Test Yayinevi",
            tax_or_mersis_no="1111111111",
            corporate_email="info@yayinevi.example",
            approval_status=ApprovalStatus.APPROVED,
        )
        pub_user = new_user("yetkili@yayinevi.example", UserRole.PUBLISHER_STAFF, org=pub_org)
        author = new_user("yazar@example.org", UserRole.AUTHOR, author_code="YZR-999901")
        licensee = new_user("alici@example.org", UserRole.LICENSEE)

        work = Work(
            isbn="9789750719387",
            title="Ornek Kitap",
            publisher=pub_org,
            metadata_source=WorkMetadataSource.MANUAL,
            registered_by=pub_user,
        )
        work.author_links.append(WorkAuthor(author=author, split_percent=Decimal("100")))

        inv = Invitation(
            email="davet@example.org",
            invited_by=pub_user,
            work=work,
            token_hash="a" * 64,
            expires_at=NOW,
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
        terms.approvals.append(TermsApproval(author=author, decision=ApprovalDecision.APPROVED))
        work.status = WorkStatus.LICENSABLE

        req = LicenseRequest(
            work=work,
            terms=terms,
            licensee_user=licensee,
            intended_use=IntendedUse.ACADEMIC_NONCOMMERCIAL,
            purpose_description="Turkce dil modeli arastirmasi",
            status=RequestStatus.AWAITING_PAYMENT,
        )
        req.approvals.append(
            RequestApproval(party=ApprovalParty.AUTHOR, user=author, decision=RequestDecision.APPROVED)
        )
        req.approvals.append(
            RequestApproval(party=ApprovalParty.PUBLISHER, user=pub_user, decision=RequestDecision.APPROVED)
        )

        pay = Payment(
            request=req,
            amount=Decimal("10000.00"),
            provider=PaymentProvider.INTERNAL_SIMULATION,
            status=PaymentStatus.SUCCEEDED,
            idempotency_key="smoke-1",
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

        lic = License(
            request=req,
            license_number="EL-2026-999901" \
            "",
            starts_on=date(2026, 12, 20),
            ends_on=date(2029, 12, 20),
        )
        lic.usage_reports.append(
            UsageReport(
                reported_by=licensee,
                usage_type=AiUsageType.PRETRAINING,
                period_start=date(2026, 12, 20),
                period_end=date(2026, 12, 31),
                model_name="deneme-model",
                description="Ilk egitim denemesi",
                declaration_accepted=True,
            )
        )

        session.add_all(
            [
                inv,
                pay,
                lic,
                AuditLog(
                    actor=licensee,
                    action="payment.succeeded",
                    entity_type="payment",
                    entity_id="1",
                    new_value={"amount": "10000.00"},
                    ip_address="127.0.0.1",
                ),
            ]
        )
        session.flush()

        total = sum(s.amount for s in pay.revenue_shares)
        assert total == pay.amount
        print(f"  OK   Tum iliskiler ve kayitlar basarili, pay toplami = {total} TL")

        print("2) Veritabani kisit testleri")

        expect_violation(
            session,
            "ayni ISBN ikinci kez",
            lambda: session.add(
                Work(
                    isbn="9789750719387",
                    title="Kopya",
                    publisher=pub_org,
                    metadata_source=WorkMetadataSource.MANUAL,
                    registered_by=pub_user,
                )
            ),
        )
        expect_violation(
            session,
            "gecersiz ISBN bicimi",
            lambda: session.add(
                Work(
                    isbn="123",
                    title="Hatali",
                    publisher=pub_org,
                    metadata_source=WorkMetadataSource.MANUAL,
                    registered_by=pub_user,
                )
            ),
        )
        expect_violation(
            session,
            "e-posta buyuk/kucuk harf farkiyla tekrar",
            lambda: session.add(new_user("YAZAR@Example.org", UserRole.LICENSEE)),
        )
        expect_violation(
            session,
            "yazar ama author_code yok",
            lambda: session.add(new_user("a2@example.org", UserRole.AUTHOR)),
        )
        expect_violation(
            session,
            "yayinevi yetkilisi ama kurum yok",
            lambda: session.add(new_user("p2@example.org", UserRole.PUBLISHER_STAFF)),
        )
        expect_violation(
            session,
            "ayni esere ayni e-posta ile ikinci bekleyen davet",
            lambda: session.add(
                Invitation(
                    email="DAVET@Example.org",
                    invited_by=pub_user,
                    work=work,
                    token_hash="b" * 64,
                    expires_at=NOW,
                )
            ),
        )
        expect_violation(
            session,
            "oranlar toplami 100 degil",
            lambda: session.add(
                LicenseTerms(
                    work=work,
                    version_no=2,
                    permission_level=PermissionLevel.ACADEMIC_ONLY,
                    author_share_pct=Decimal("60"),
                    publisher_share_pct=Decimal("30"),
                    status=TermsStatus.PROPOSED,
                    proposed_by=pub_user,
                )
            ),
        )
        expect_violation(
            session,
            "ayni eserde ikinci approved kosul",
            lambda: session.add(
                LicenseTerms(
                    work=work,
                    version_no=2,
                    permission_level=PermissionLevel.ACADEMIC_ONLY,
                    author_share_pct=Decimal("50"),
                    publisher_share_pct=Decimal("50"),
                    status=TermsStatus.APPROVED,
                    proposed_by=pub_user,
                )
            ),
        )
        expect_violation(
            session,
            "ayni kisi ayni talebe ikinci kez onay veriyor",
            lambda: session.add(
                RequestApproval(
                    request=req,
                    party=ApprovalParty.AUTHOR,
                    user=author,
                    decision=RequestDecision.APPROVED,
                )
            ),
        )
        expect_violation(
            session,
            "ayni talebe ikinci basarili odeme",
            lambda: session.add(
                Payment(
                    request=req,
                    amount=Decimal("10000.00"),
                    provider=PaymentProvider.INTERNAL_SIMULATION,
                    status=PaymentStatus.SUCCEEDED,
                    idempotency_key="smoke-2",
                )
            ),
        )
        expect_violation(
            session,
            "basarisiz odeme ama hata nedeni bos",
            lambda: session.add(
                Payment(
                    request=req,
                    amount=Decimal("10000.00"),
                    provider=PaymentProvider.INTERNAL_SIMULATION,
                    status=PaymentStatus.FAILED,
                    idempotency_key="smoke-3",
                )
            ),
        )
        expect_violation(
            session,
            "ayni odemede ikinci platform gelir payi",
            lambda: session.add(
                RevenueShare(
                    payment=pay,
                    recipient_type=RecipientType.PLATFORM,
                    share_percent=Decimal("15"),
                    amount=Decimal("1500.00"),
                )
            ),
        )
        expect_violation(
            session,
            "kullanim raporunda beyan onayi false",
            lambda: session.add(
                UsageReport(
                    license=lic,
                    reported_by=licensee,
                    usage_type=AiUsageType.FINE_TUNING,
                    period_start=date(2026, 12, 20),
                    period_end=date(2026, 12, 31),
                    description="Test",
                    declaration_accepted=False,
                )
            ),
        )
        expect_violation(
            session,
            "gecersiz durum degeri (ham SQL)",
            lambda: session.execute(
                text("UPDATE works SET status = 'bogus' WHERE id = :i"),
                {"i": work.id},
            ),
        )

        print("\nTUM KONTROLLER BASARIYLA GECTI!")
    finally:
        session.rollback()
        session.close()


if __name__ == "__main__":
    main()