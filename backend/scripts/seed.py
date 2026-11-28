"""Baslangic verisi (seed) yukleyici. TEKRAR CALISTIRILABILIR: ikinci calistirmada mevcut kayitlari atlar.

Kullanim (backend/ klasorunden, alembic upgrade head + db_roles.sql sonrasi):

    python -m scripts.seed                 # 1) ISBN kataloglari + admin hesabi
    python -m scripts.seed --dev           # 2) + ornek kurum ve kullanicilar (gelistirme)
    python -m scripts.seed --demo          # 3) + ornek eser/talep/odeme/lisans (arayuz gelistirme)

    --no-sample          seed/books_sample.csv yuklenmesin (gercek pilot ortami icin)
    --catalog DOSYA ...  varsayilan CSV'ler yerine bu dosyalari yukle

Sifreler .env icindeki SEED_PASSWORD'den okunur ve Argon2 ile ozetlenir; depoda duz metin yoktur.
"""
import argparse
import csv
import hashlib
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import ayarlar
from app.database import OturumFabrikasi

settings = ayarlar()
from app.isbn import InvalidIsbnError, normalize_isbn
from app.models import (
    AuditLog, Invitation, IsbnCatalog, License, LicenseRequest, LicenseTerms, Organization, Payment,
    RequestApproval, RevenueShare, TermsApproval, UsageReport, User, Work, WorkAuthor,
)
from app.models.enums import (
    AiUsageType,ApprovalDecision, ApprovalParty, ApprovalStatus, CatalogSource, IntendedUse, InvitationStatus,
    OrganizationType, PaymentFailureReason, PaymentProvider, PaymentStatus, PermissionLevel,
    RecipientType, RequestDecision, RequestStatus, TermsStatus, UserRole, WorkMetadataSource,
    WorkStatus,
)
from app.security import hash_password

SEED_DIR = Path(__file__).resolve().parent.parent / "seed"
DEFAULT_CATALOGS = [SEED_DIR / "books_sample.csv", SEED_DIR / "books_pilot.csv"]
NOW = datetime.now(timezone.utc)


# ===========================================================================
# 1) ISBN katalogu (CSV -> isbn_catalog)
# ===========================================================================
@dataclass
class CatalogRow:
    isbn: str
    title: str
    authors: str | None
    publisher: str | None
    year: int | None


def _read_text(path: Path) -> str:
    """Excel (Turkce Windows) CSV'leri UTF-8 degil Windows-1254 olabilir: ikisini de dener."""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1254"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{path.name}: dosya kodlamasi taninamadi (UTF-8 olarak kaydedin)")


def read_catalog_csv(path: Path) -> tuple[list[CatalogRow], list[str]]:
    """CSV'yi okur. (gecerli satirlar, hata mesajlari) dondurur. Ayirici ',' veya ';' olabilir."""
    text = _read_text(path)
    if not text.strip():
        return [], []
    dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t")
    reader = csv.DictReader(text.splitlines(), dialect=dialect)
    reader.fieldnames = [(h or "").strip().lower() for h in (reader.fieldnames or [])]

    missing = {"isbn", "title"} - set(reader.fieldnames)
    if missing:
        return [], [f"{path.name}: zorunlu sutun(lar) yok: {', '.join(sorted(missing))}"]

    rows, errors, seen = [], [], {}
    for line_no, rec in enumerate(reader, start=2):  # 1. satir baslik
        if not any((v or "").strip() for v in rec.values()):
            continue  # tamamen bos satir
        where = f"{path.name} satir {line_no}"
        try:
            isbn = normalize_isbn(rec.get("isbn") or "")
        except InvalidIsbnError as exc:
            errors.append(f"{where}: {exc}")
            continue
        title = (rec.get("title") or "").strip()
        if not title:
            errors.append(f"{where}: baslik bos")
            continue
        if len(title) > 300:
            errors.append(f"{where}: baslik 300 karakterden uzun")
            continue
        year_raw = (rec.get("year") or "").strip()
        year = None
        if year_raw:
            if not (year_raw.isdigit() and 1400 <= int(year_raw) <= date.today().year + 1):
                errors.append(f"{where}: yil gecersiz: {year_raw!r}")
                continue
            year = int(year_raw)
        if isbn in seen:
            errors.append(f"{where}: ISBN {isbn} dosyada {seen[isbn]}. satirda da var")
            continue
        seen[isbn] = line_no
        rows.append(CatalogRow(isbn, title, (rec.get("authors") or "").strip() or None,
                               (rec.get("publisher") or "").strip() or None, year))
    return rows, errors


def load_catalog(session: Session, rows: list[CatalogRow]) -> tuple[int, int]:
    """Toplu ekleme; ISBN zaten varsa ATLAR (ON CONFLICT DO NOTHING). (eklenen, atlanan) dondurur.

    DO NOTHING bilerek secildi: katalogda elle yapilmis duzeltmeler, betik her calistiginda ezilmesin.
    """
    if not rows:
        return 0, 0
    values = [
        {"isbn": r.isbn, "title": r.title, "authors_text": r.authors, "publisher_name": r.publisher,
         "publication_year": r.year, "source": CatalogSource.SEED}
        for r in rows
    ]
    stmt = (pg_insert(IsbnCatalog).values(values)
            .on_conflict_do_nothing(index_elements=[IsbnCatalog.isbn])
            .returning(IsbnCatalog.isbn))
    inserted = len(session.execute(stmt).scalars().all())
    return inserted, len(rows) - inserted


# ===========================================================================
# 2) Kurum ve kullanici yardimcilari (varsa dokunma, yoksa olustur)
# ===========================================================================
def get_or_create_org(session: Session, *, tax_no: str, **fields) -> tuple[Organization, bool]:
    org = session.scalar(select(Organization).where(Organization.tax_or_mersis_no == tax_no))
    if org:
        return org, False
    org = Organization(tax_or_mersis_no=tax_no, approval_status=ApprovalStatus.APPROVED,
                       reviewed_at=NOW, **fields)
    session.add(org)
    session.flush()
    return org, True


def get_or_create_user(session: Session, *, email: str, password: str, **fields) -> tuple[User, bool]:
    email = email.lower()  # uygulama kurali: e-posta kucuk harfle saklanir
    user = session.scalar(select(User).where(func.lower(User.email) == email))
    if user:
        return user, False
    user = User(email=email, password_hash=hash_password(password),
                privacy_notice_accepted_at=NOW, **fields)
    session.add(user)
    session.flush()
    return user, True


def seed_admin(session: Session, password: str) -> bool:
    _, created = get_or_create_user(
        session, email=settings.seed_admin_email, password=password,
        first_name="Sistem", last_name="Yoneticisi", role=UserRole.ADMIN)
    return created


@dataclass
class DevAccounts:
    publisher_org: Organization
    licensee_org: Organization
    publisher_staff: User
    authors: list[User]
    licensee: User
    created: int


def seed_dev_accounts(session: Session, password: str) -> DevAccounts:
    """Gelistirme icin her rolden ornek hesap. ALAN ADI '.example' (RFC 2606): gercek e-posta degildir.

    Pilot katilimcilari BU hesaplari kullanmaz; kendileri takma adla kayit olur (Fizibilite Bolum 5.1).
    """
    created = 0
    pub_org, c = get_or_create_org(
        session, tax_no="1000000001", type=OrganizationType.PUBLISHER, name="Örnek Yayınevi",
        corporate_email="iletisim@ornek-yayinevi.example")
    created += c
    lic_org, c = get_or_create_org(
        session, tax_no="1000000002", type=OrganizationType.LICENSEE, name="Örnek NLP Araştırma Ekibi",
        corporate_email="iletisim@ornek-nlp.example")
    created += c

    staff, c = get_or_create_user(
        session, email="yetkili@ornek-yayinevi.example", password=password, first_name="Örnek",
        last_name="Yetkili", role=UserRole.PUBLISHER_STAFF, organization_id=pub_org.id,
        pilot_consent_at=NOW)
    created += c
    authors = []
    for i in range(1, 4):
        a, c = get_or_create_user(
            session, email=f"yazar{i}@ornek.example", password=password, first_name="Örnek",
            last_name=f"Yazar {i}", role=UserRole.AUTHOR, author_code=f"YZR-{i:06d}",
            pilot_consent_at=NOW)
        authors.append(a)
        created += c
    licensee, c = get_or_create_user(
        session, email="alici@ornek-nlp.example", password=password, first_name="Örnek",
        last_name="Alıcı", role=UserRole.LICENSEE, organization_id=lic_org.id, pilot_consent_at=NOW)
    created += c
    return DevAccounts(pub_org, lic_org, staff, authors, licensee, created)


# ===========================================================================
# 3) Demo verisi: arayuz ve rapor gelistirme icin ornek is akisi
# ===========================================================================
def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    last_day = (date(year + (month // 12), month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(d.day, last_day))


def seed_demo(session: Session, acc: DevAccounts) -> bool:
    """3 eser: (A) onay bekliyor, (B) lisanslanabilir + bekleyen talep, (C) lisansli + odenmis.

    Tek islemde yazilir; gelir paylari toplami odeme tutarina esit olmali (veritabani COMMIT'te dogrular).
    """
    isbns = [r[0] for r in session.execute(
        select(IsbnCatalog.isbn).where(IsbnCatalog.isbn.like("9790100000%")).order_by(IsbnCatalog.isbn).limit(3))]
    if len(isbns) < 3:
        print("  ! Demo icin seed/books_sample.csv katalogda olmali (--no-sample kullanmayin).")
        return False
    if session.scalar(select(Work.id).where(Work.isbn == isbns[0])):
        return False  # demo zaten yuklu

    pub, staff, licensee = acc.publisher_org, acc.publisher_staff, acc.licensee
    a1, a2, a3 = acc.authors

    def new_work(isbn: str, authors: list[tuple[User, str]], status: WorkStatus) -> Work:
        cat = session.get(IsbnCatalog, isbn)
        w = Work(isbn=isbn, title=cat.title, publisher=pub, publication_year=cat.publication_year,
                 metadata_source=WorkMetadataSource.LOCAL_CATALOG, registered_by=staff, status=status)
        for user, split in authors:
            w.author_links.append(WorkAuthor(author=user, split_percent=Decimal(split)))
        session.add(w)
        return w

    def new_terms(w: Work, level: PermissionLevel, fee: str, author_pct: str, status: TermsStatus) -> LicenseTerms:
        t = LicenseTerms(work=w, version_no=1, permission_level=level, fee_amount=Decimal(fee),
                         author_share_pct=Decimal(author_pct), publisher_share_pct=100 - Decimal(author_pct),
                         status=status, proposed_by=staff)
        session.add(t)
        return t

    # --- Eser A: yazar onayi bekliyor; ikinci yazar davetli (henuz kayitsiz) ---
    work_a = new_work(isbns[0], [(a1, "50")], WorkStatus.PENDING_APPROVAL)
    terms_a = new_terms(work_a, PermissionLevel.ACADEMIC_ONLY, "10000", "60", TermsStatus.PROPOSED)
    session.add(Invitation(
        email="yeni.yazar@ornek.example", invited_by=staff, work=work_a, split_percent=Decimal("50"),
        token_hash=hashlib.sha256(b"demo-davet-tokeni").hexdigest(),
        status=InvitationStatus.PENDING, expires_at=NOW + timedelta(days=14)))

    # --- Eser B: lisanslanabilir (ticari dahil); bir talep hak sahibi onayi bekliyor ---
    work_b = new_work(isbns[1], [(a2, "100")], WorkStatus.LICENSABLE)
    terms_b = new_terms(work_b, PermissionLevel.COMMERCIAL_INCLUDED, "25000", "50", TermsStatus.APPROVED)
    terms_b.approvals.append(TermsApproval(author=a2, decision=ApprovalDecision.APPROVED))
    req_b = LicenseRequest(
        work=work_b, terms=terms_b, licensee_user=licensee, licensee_org=acc.licensee_org,
        intended_use=IntendedUse.COMMERCIAL, purpose_description="Turkce ticari dil modeli egitimi",
        status=RequestStatus.AWAITING_APPROVAL)
    session.add(req_b)

    # --- Eser C: tam akis: onayli kosul, iki onay, basarisiz + basarili odeme, aktif lisans ---
    work_c = new_work(isbns[2], [(a3, "100")], WorkStatus.LICENSABLE)
    terms_c = new_terms(work_c, PermissionLevel.ACADEMIC_ONLY, "10000", "60", TermsStatus.APPROVED)
    terms_c.approvals.append(TermsApproval(author=a3, decision=ApprovalDecision.APPROVED))
    req_c = LicenseRequest(
        work=work_c, terms=terms_c, licensee_user=licensee, licensee_org=acc.licensee_org,
        intended_use=IntendedUse.ACADEMIC_NONCOMMERCIAL,
        purpose_description="Turkce dogal dil isleme arastirmasi", status=RequestStatus.ACTIVE)
    req_c.approvals += [
        RequestApproval(party=ApprovalParty.AUTHOR, user=a3, decision=RequestDecision.APPROVED),
        RequestApproval(party=ApprovalParty.PUBLISHER, user=staff, decision=RequestDecision.APPROVED),
    ]
    failed = Payment(
        request=req_c, amount=Decimal("10000.00"), provider=PaymentProvider.INTERNAL_SIMULATION,
        status=PaymentStatus.FAILED, failure_reason=PaymentFailureReason.INSUFFICIENT_FUNDS,
        idempotency_key="demo-odeme-basarisiz-1", completed_at=NOW)
    paid = Payment(
        request=req_c, amount=Decimal("10000.00"), provider=PaymentProvider.INTERNAL_SIMULATION,
        status=PaymentStatus.SUCCEEDED, provider_reference="SIM-0001",
        idempotency_key="demo-odeme-basarili-1", completed_at=NOW)
    # 10.000 TL: platform %15 = 1.500; kalan 8.500'un %60'i yazara = 5.100; %40'i yayinevine = 3.400
    paid.revenue_shares += [
        RevenueShare(recipient_type=RecipientType.PLATFORM, share_percent=Decimal("15"), amount=Decimal("1500.00")),
        RevenueShare(recipient_type=RecipientType.AUTHOR, recipient_user=a3, share_percent=Decimal("51"), amount=Decimal("5100.00")),
        RevenueShare(recipient_type=RecipientType.PUBLISHER, recipient_org=pub, share_percent=Decimal("34"), amount=Decimal("3400.00")),
    ]
    today = date.today()
    lic = License(request=req_c, license_number=f"EL-{today.year}-000001", starts_on=today,
                  ends_on=add_months(today, 36))
    lic.usage_reports.append(UsageReport(
    reported_by=licensee,
    usage_type=AiUsageType.PRETRAINING,
    period_start=today,
    period_end=today + timedelta(days=30),
    model_name="demo-model-1",
    description="Ilk egitim denemesi (demo)",
    declaration_accepted=True,
))
    session.add_all([failed, paid, lic])
    session.flush()

    # Denetim gunlugu: yonetici ekraninda gorunecek temel olaylar (is kayitlariyla ayni islem)
    for actor, action, etype, eid in [
        (staff, "work.registered", "work", work_a.id), (staff, "work.registered", "work", work_b.id),
        (staff, "work.registered", "work", work_c.id), (a3, "terms.approved", "license_terms", terms_c.id),
        (licensee, "request.created", "license_request", req_c.id),
        (licensee, "payment.failed", "payment", failed.id), (licensee, "payment.succeeded", "payment", paid.id),
        (licensee, "request.created", "license_request", req_b.id),
    ]:
        session.add(AuditLog(actor_user_id=actor.id, action=action, entity_type=etype, entity_id=str(eid)))
    return True



# Ana akis 
def main() -> int:
    parser = argparse.ArgumentParser(description="EserLisans seed yukleyici")
    parser.add_argument("--dev", action="store_true", help="ornek kurum ve kullanicilar")
    parser.add_argument("--demo", action="store_true", help="ornek eser/talep/odeme/lisans (--dev'i icerir)")
    parser.add_argument("--no-sample", action="store_true", help="books_sample.csv yuklenmesin")
    parser.add_argument("--catalog", nargs="+", type=Path, help="CSV dosyalari (varsayilanlarin yerine)")
    args = parser.parse_args()
    if args.demo:
        args.dev = True

    password = settings.seed_password
    if not password:
        print("HATA: .env icinde SEED_PASSWORD tanimli degil (admin/gelistirme hesaplari icin gerekli).")
        return 1
    if len(password) < 8:
        print("HATA: SEED_PASSWORD en az 8 karakter olmali.")
        return 1

    files = args.catalog or [p for p in DEFAULT_CATALOGS if not (args.no_sample and p.name == "books_sample.csv")]

    #  1) tum CSV'leri once DOGRULA; tek hata varsa hicbir sey yazma 
    all_rows, all_errors = [], []
    for path in files:
        if not path.exists():
            all_errors.append(f"{path}: dosya bulunamadi")
            continue
        rows, errors = read_catalog_csv(path)
        print(f"  {path.name}: {len(rows)} gecerli satir, {len(errors)} hata")
        all_rows += rows
        all_errors += errors
    if all_errors:
        print("\nCSV HATALARI (hicbir kayit yazilmadi):")
        print("  - " + "\n  - ".join(all_errors))
        return 1

    #  2) tek islemde yaz 
    with OturumFabrikasi() as session:
        try:
            # ayni ISBN iki dosyada varsa ilki gecerli
            unique = list({r.isbn: r for r in reversed(all_rows)}.values())[::-1]
            inserted, skipped = load_catalog(session, unique)
            print(f"\nISBN katalogu : {inserted} eklendi, {skipped} zaten vardi")

            admin_created = seed_admin(session, password)
            print(f"Admin hesabi  : {settings.seed_admin_email} ({'olusturuldu' if admin_created else 'zaten vardi'})")

            if args.dev:
                acc = seed_dev_accounts(session, password)
                print(f"Gelistirme    : {acc.created} yeni kayit (kurum + kullanici)")
                if args.demo:
                    print("Demo verisi   :", "yuklendi" if seed_demo(session, acc) else "atlandi (zaten yuklu veya katalog eksik)")

            session.commit()  # (demo'da) gelir payi toplami kontrolu burada calisir
        except Exception:
            session.rollback()
            raise
    print("\nTamam.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
