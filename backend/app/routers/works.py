"""Eser yönetimi, hibrit ISBN, yazar daveti, teklif-onay ve arama uçları (Sprint 3)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, require_approved_publisher, require_roles
from app.config import ayarlar
from app.core.audit import get_client_ip, record_audit
from app.core.email import send_transactional_email
from app.database import get_db
from app.models.catalog import IsbnCatalog, LicenseTerms, TermsApproval, Work, WorkAuthor
from app.models.enums import (
    ApprovalDecision,
    CatalogSource,
    InvitationStatus,
    PermissionLevel,
    TermsStatus,
    UserRole,
    WorkMetadataSource,
    WorkStatus,
)
from app.models.identity import Invitation, User
from app.schemas.works import (
    AuthorInviteRequest,
    AuthorLookupResponse,
    InvitationResponse,
    IsbnLookupResponse,
    LicenseTermsResponse,
    MarkUnavailableRequest,
    TermsDecisionRequest,
    TermsProposalInput,
    WorkAuthorResponse,
    WorkCreateRequest,
    WorkResponse,
)
from app.services.isbn_service import (
    lookup_isbn_hybrid,
    normalize_turkish,
    validate_and_normalize_isbn,
)

router = APIRouter(prefix="/api/works", tags=["Eser Yönetimi"])


def _load_work(db: Session, work_id: int) -> Work | None:
    stmt = (
        select(Work)
        .where(Work.id == work_id)
        .options(
            selectinload(Work.publisher),
            selectinload(Work.author_links).selectinload(WorkAuthor.author),
            selectinload(Work.terms_versions).selectinload(LicenseTerms.approvals),
        )
    )
    return db.scalar(stmt.execution_options(populate_existing=True))


def _serialize_work(db: Session, work: Work) -> WorkResponse:
    pending_invites = list(
        db.scalars(
            select(Invitation).where(
                Invitation.work_id == work.id,
                Invitation.status == InvitationStatus.PENDING,
            )
        ).all()
    )
    terms_sorted = sorted(work.terms_versions, key=lambda t: t.version_no)
    approved_terms = next((t for t in reversed(terms_sorted) if t.status == TermsStatus.APPROVED), None)
    current_terms = approved_terms or (terms_sorted[-1] if terms_sorted else None)

    return WorkResponse(
        id=work.id,
        isbn=work.isbn,
        title=work.title,
        publisher_org_id=work.publisher_org_id,
        publisher_name=work.publisher.name if work.publisher else None,
        publication_year=work.publication_year,
        language_code=work.language_code,
        category=work.category,
        metadata_source=work.metadata_source,
        status=work.status,
        registered_by_user_id=work.registered_by_user_id,
        created_at=work.created_at,
        updated_at=work.updated_at,
        authors=[
            WorkAuthorResponse(
                author_id=link.author_id,
                author_code=link.author.author_code,
                first_name=link.author.first_name,
                last_name=link.author.last_name,
                email=link.author.email,
                split_percent=link.split_percent,
            )
            for link in work.author_links
        ],
        pending_invitations=[InvitationResponse.model_validate(inv) for inv in pending_invites],
        current_terms=(
            LicenseTermsResponse.model_validate(current_terms) if current_terms else None
        ),
        terms_versions=[LicenseTermsResponse.model_validate(t) for t in terms_sorted],
    )


def _create_invitation(
    db: Session,
    *,
    work: Work,
    email: str,
    split_percent: Decimal,
    invited_by_user_id: int,
) -> tuple[Invitation, str]:
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    inv = Invitation(
        email=email.strip().lower(),
        invited_by_user_id=invited_by_user_id,
        work_id=work.id,
        split_percent=split_percent,
        token_hash=token_hash,
        status=InvitationStatus.PENDING,
        expires_at=datetime.now(timezone.utc) + timedelta(days=14),
    )
    db.add(inv)
    db.flush()
    return inv, raw_token


def _send_invitation_email(email: str, work: Work, raw_token: str) -> None:
    invite_url = f"{ayarlar().uygulama_adresi}/register?invitation_token={raw_token}"
    send_transactional_email(
        to_email=email,
        subject=f"EserLisans yazar daveti: {work.title}",
        body=(
            f"'{work.title}' (ISBN: {work.isbn}) eseri için yazar olarak davet edildiniz. "
            f"Kayıt olup koşulları incelemek için bağlantı: {invite_url}"
        ),
    )


def _ensure_work_visible(work: Work, user: User) -> None:
    if user.role == UserRole.ADMIN:
        return
    if user.role == UserRole.PUBLISHER_STAFF and work.publisher_org_id == user.organization_id:
        return
    if user.role == UserRole.AUTHOR and any(link.author_id == user.id for link in work.author_links):
        return
    if user.role == UserRole.LICENSEE and work.status == WorkStatus.LICENSABLE:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Bu eseri görüntüleme yetkiniz bulunmamaktadır.",
    )


@router.get(
    "/isbn/{isbn}",
    response_model=IsbnLookupResponse,
    summary="ISBN künyesini hibrit servisten sorgular (SCRUM-44 / BFR-06, BFR-07)",
)
def lookup_isbn(
    isbn: str,
    _: User = Depends(require_approved_publisher),
    db: Session = Depends(get_db),
) -> IsbnLookupResponse:
    try:
        result = lookup_isbn_hybrid(db, isbn)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    return IsbnLookupResponse(
        isbn=result.isbn,
        found=result.found,
        metadata_source=result.metadata_source,
        title=result.title,
        authors_text=result.authors_text,
        publisher_name=result.publisher_name,
        publication_year=result.publication_year,
        language_code=result.language_code,
        category=result.category,
    )


@router.get(
    "/authors/lookup",
    response_model=AuthorLookupResponse,
    summary="Yazar ID veya e-posta ile kayıtlı yazarı arar (SCRUM-46 / BFR-08)",
)
def lookup_author(
    author_code: str | None = Query(default=None),
    email: str | None = Query(default=None),
    _: User = Depends(require_approved_publisher),
    db: Session = Depends(get_db),
) -> AuthorLookupResponse:
    if not author_code and not email:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="author_code veya email parametresi verilmelidir.",
        )

    stmt = select(User).where(User.role == UserRole.AUTHOR, User.is_active.is_(True))
    if author_code:
        stmt = stmt.where(User.author_code == author_code.strip().upper())
    else:
        stmt = stmt.where(func.lower(User.email) == (email or "").strip().lower())

    author = db.scalar(stmt)
    if author is None or author.author_code is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kayıtlı yazar bulunamadı; eser kaydında e-posta ile davet gönderebilirsiniz.",
        )
    return AuthorLookupResponse(
        id=author.id,
        author_code=author.author_code,
        first_name=author.first_name,
        last_name=author.last_name,
        email=author.email,
    )


@router.post(
    "",
    response_model=WorkResponse,
    status_code=status.HTTP_201_CREATED,
    summary="ISBN ile eser kaydeder, yazar seçer/davet eder ve ilk teklifi oluşturur (UC4, UC8, UC9)",
)
def create_work(
    payload: WorkCreateRequest,
    request: Request,
    publisher_user: User = Depends(require_approved_publisher),
    db: Session = Depends(get_db),
) -> WorkResponse:
    try:
        isbn = validate_and_normalize_isbn(payload.isbn)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    existing_work = db.scalar(select(Work).where(Work.isbn == isbn))
    if existing_work is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bu ISBN numarası ile kayıtlı bir eser zaten mevcut.",
        )

    lookup = lookup_isbn_hybrid(db, isbn)
    resolved_title = (payload.title or lookup.title or "").strip()
    if not resolved_title:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Künye dış servislerde bulunamadı; eser başlığını (title) elle girmelisiniz.",
        )

    metadata_source = lookup.metadata_source if lookup.found else WorkMetadataSource.MANUAL
    resolved_year = payload.publication_year if payload.publication_year is not None else lookup.publication_year
    resolved_lang = (payload.language_code or lookup.language_code or "tr").strip()
    resolved_category = payload.category if payload.category is not None else lookup.category

    # Künye bulunamadıysa elle girilen bilgiyi yerel kataloğa da ekle (Tablo 7)
    if not lookup.found and db.get(IsbnCatalog, isbn) is None:
        db.add(
            IsbnCatalog(
                isbn=isbn,
                title=resolved_title,
                publisher_name=publisher_user.organization.name if publisher_user.organization else None,
                publication_year=resolved_year,
                language_code=resolved_lang,
                category=resolved_category,
                source=CatalogSource.MANUAL,
            )
        )

    # Yazarları çözümle (kayıtlı yazarlar vs davet edilecek e-postalar)
    resolved_authors: list[tuple[User, Decimal]] = []
    invite_targets: list[tuple[str, Decimal]] = []
    seen_author_ids: set[int] = set()
    seen_invite_emails: set[str] = set()

    for author_in in payload.authors:
        author: User | None = None
        if author_in.author_code:
            author = db.scalar(
                select(User).where(
                    User.role == UserRole.AUTHOR,
                    User.author_code == author_in.author_code.strip().upper(),
                )
            )
            if author is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Yazar kodu bulunamadı: {author_in.author_code}",
                )
        elif author_in.email:
            norm_email = author_in.email.strip().lower()
            author = db.scalar(
                select(User).where(
                    User.role == UserRole.AUTHOR,
                    func.lower(User.email) == norm_email,
                )
            )
            if author is None:
                if norm_email in seen_invite_emails:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"Aynı e-posta birden fazla kez eklenemez: {norm_email}",
                    )
                seen_invite_emails.add(norm_email)
                invite_targets.append((norm_email, author_in.split_percent))
                continue

        if author is not None:
            if author.id in seen_author_ids:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Aynı yazar bir esere birden fazla kez eklenemez.",
                )
            seen_author_ids.add(author.id)
            resolved_authors.append((author, author_in.split_percent))

    work = Work(
        isbn=isbn,
        title=resolved_title,
        publisher_org_id=publisher_user.organization_id,
        publication_year=resolved_year,
        language_code=resolved_lang,
        category=resolved_category,
        metadata_source=metadata_source,
        status=WorkStatus.PENDING_APPROVAL,
        registered_by_user_id=publisher_user.id,
    )
    db.add(work)
    db.flush()

    for author, split_pct in resolved_authors:
        db.add(WorkAuthor(work_id=work.id, author_id=author.id, split_percent=split_pct))

    sent_invites: list[tuple[str, str]] = []
    for invite_email, split_pct in invite_targets:
        _, raw_token = _create_invitation(
            db,
            work=work,
            email=invite_email,
            split_percent=split_pct,
            invited_by_user_id=publisher_user.id,
        )
        sent_invites.append((invite_email, raw_token))

    terms = LicenseTerms(
        work_id=work.id,
        version_no=1,
        permission_level=payload.terms.permission_level,
        fee_amount=payload.terms.fee_amount,
        currency=payload.terms.currency.upper(),
        duration_months=payload.terms.duration_months,
        author_share_pct=payload.terms.author_share_pct,
        publisher_share_pct=payload.terms.publisher_share_pct,
        status=TermsStatus.PROPOSED,
        proposed_by_user_id=publisher_user.id,
        revision_note=payload.terms.revision_note,
    )
    db.add(terms)
    db.flush()

    record_audit(
        db,
        actor_user_id=publisher_user.id,
        action="work.created",
        entity_type="work",
        entity_id=work.id,
        new_value={
            "isbn": work.isbn,
            "title": work.title,
            "metadata_source": work.metadata_source.value,
            "terms_version": 1,
            "permission_level": terms.permission_level.value,
            "fee_amount": str(terms.fee_amount),
            "invited_emails": [e for e, _ in sent_invites],
        },
        ip_address=get_client_ip(request),
    )
    db.commit()

    # İşlem bildirimleri (BFR-31)
    for invite_email, raw_token in sent_invites:
        _send_invitation_email(invite_email, work, raw_token)
    for author, _ in resolved_authors:
        send_transactional_email(
            to_email=author.email,
            subject=f"Onayınızı bekleyen eser kaydı: {work.title}",
            body=(
                f"'{work.title}' (ISBN: {work.isbn}) eseri yayınevi tarafından kaydedildi. "
                "İzin seviyesi ve lisans koşullarını panelinizden onaylayabilir veya revizyon talep edebilirsiniz."
            ),
        )

    loaded = _load_work(db, work.id)
    assert loaded is not None
    return _serialize_work(db, loaded)


@router.get(
    "",
    response_model=list[WorkResponse],
    summary="Rol bazlı eser listeleme ve Türkçe karakter destekli arama (UC6, UC7 / SCRUM-85)",
)
def list_and_search_works(
    q: str | None = Query(default=None, description="Başlık, yazar adı veya ISBN araması"),
    title: str | None = Query(default=None),
    author: str | None = Query(default=None),
    isbn: str | None = Query(default=None),
    permission_level: PermissionLevel | None = Query(default=None),
    work_status: WorkStatus | None = Query(default=None, alias="status"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[WorkResponse]:
    stmt = select(Work).options(
        selectinload(Work.publisher),
        selectinload(Work.author_links).selectinload(WorkAuthor.author),
        selectinload(Work.terms_versions).selectinload(LicenseTerms.approvals),
    )

    # BFR-14: Rol bazlı görünürlük
    if current_user.role == UserRole.LICENSEE:
        stmt = stmt.where(Work.status == WorkStatus.LICENSABLE)
    elif current_user.role == UserRole.PUBLISHER_STAFF:
        stmt = stmt.where(Work.publisher_org_id == current_user.organization_id)
    elif current_user.role == UserRole.AUTHOR:
        stmt = stmt.join(WorkAuthor, WorkAuthor.work_id == Work.id).where(
            WorkAuthor.author_id == current_user.id
        )

    if work_status is not None and current_user.role != UserRole.LICENSEE:
        stmt = stmt.where(Work.status == work_status)

    if isbn:
        cleaned_isbn = isbn.replace("-", "").replace(" ", "").strip()
        try:
            cleaned_isbn = validate_and_normalize_isbn(cleaned_isbn)
        except ValueError:
            pass
        stmt = stmt.where(Work.isbn == cleaned_isbn)

    works = list(db.scalars(stmt.order_by(Work.created_at.desc()).execution_options(populate_existing=True)).all())

    # Türkçe karakter duyarlı (İ/ı/ş/ğ/ç/ö/ü/â) filtreleme (SCRUM-85)
    q_norm = normalize_turkish(q)
    title_norm = normalize_turkish(title)
    author_norm = normalize_turkish(author)

    filtered: list[Work] = []
    for work in works:
        work_title_norm = normalize_turkish(work.title)
        author_haystack = " ".join(
            normalize_turkish(f"{link.author.first_name} {link.author.last_name} {link.author.author_code or ''}")
            for link in work.author_links
        )
        if title_norm and title_norm not in work_title_norm:
            continue
        if author_norm and author_norm not in author_haystack:
            continue
        if q_norm:
            q_digits = q_norm.replace("-", "").replace(" ", "")
            if (
                q_norm not in work_title_norm
                and q_norm not in author_haystack
                and (not q_digits or q_digits not in work.isbn)
            ):
                continue

        if permission_level is not None:
            terms_sorted = sorted(work.terms_versions, key=lambda t: t.version_no)
            active_terms = next(
                (t for t in reversed(terms_sorted) if t.status == TermsStatus.APPROVED),
                terms_sorted[-1] if terms_sorted else None,
            )
            if active_terms is None or active_terms.permission_level != permission_level:
                continue

        filtered.append(work)

    return [_serialize_work(db, w) for w in filtered]


@router.get(
    "/{work_id}",
    response_model=WorkResponse,
    summary="Eser ayrıntılarını rol bazlı olarak döndürür (UC7)",
)
def get_work_detail(
    work_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkResponse:
    work = _load_work(db, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Eser bulunamadı.")
    _ensure_work_visible(work, current_user)
    return _serialize_work(db, work)


@router.post(
    "/{work_id}/terms",
    response_model=WorkResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Yayınevi yeni koşul sürümü (revizyon) önerir (UC8, UC9 / BFR-11)",
)
def propose_new_terms(
    work_id: int,
    payload: TermsProposalInput,
    request: Request,
    publisher_user: User = Depends(require_approved_publisher),
    db: Session = Depends(get_db),
) -> WorkResponse:
    work = _load_work(db, work_id)
    if work is None or work.publisher_org_id != publisher_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Eser bulunamadı.")

    # Eski sürümleri superseded yap (license_terms_guard ile uyumlu)
    max_version = 0
    for old_terms in work.terms_versions:
        max_version = max(max_version, old_terms.version_no)
        if old_terms.status in (
            TermsStatus.PROPOSED,
            TermsStatus.REVISION_REQUESTED,
            TermsStatus.APPROVED,
        ):
            old_terms.status = TermsStatus.SUPERSEDED
    db.flush()

    new_terms = LicenseTerms(
        work_id=work.id,
        version_no=max_version + 1,
        permission_level=payload.permission_level,
        fee_amount=payload.fee_amount,
        currency=payload.currency.upper(),
        duration_months=payload.duration_months,
        author_share_pct=payload.author_share_pct,
        publisher_share_pct=payload.publisher_share_pct,
        status=TermsStatus.PROPOSED,
        proposed_by_user_id=publisher_user.id,
        revision_note=payload.revision_note,
    )
    db.add(new_terms)
    work.status = WorkStatus.PENDING_APPROVAL
    db.flush()

    record_audit(
        db,
        actor_user_id=publisher_user.id,
        action="work.terms_proposed",
        entity_type="license_terms",
        entity_id=new_terms.id,
        new_value={
            "work_id": work.id,
            "version_no": new_terms.version_no,
            "permission_level": new_terms.permission_level.value,
            "fee_amount": str(new_terms.fee_amount),
            "author_share_pct": str(new_terms.author_share_pct),
            "publisher_share_pct": str(new_terms.publisher_share_pct),
        },
        ip_address=get_client_ip(request),
    )
    db.commit()

    for link in work.author_links:
        send_transactional_email(
            to_email=link.author.email,
            subject=f"Eser koşulları güncellendi (v{new_terms.version_no}): {work.title}",
            body="Yayınevi yeni lisans koşulları önerdi. Lütfen panelinizden inceleyip kararınızı bildirin.",
        )

    loaded = _load_work(db, work.id)
    assert loaded is not None
    return _serialize_work(db, loaded)


@router.post(
    "/{work_id}/terms/{terms_id}/decision",
    response_model=WorkResponse,
    summary="Yazar koşul teklifini onaylar veya revizyon talep eder (UC5, UC8 / SCRUM-45)",
)
def decide_on_terms(
    work_id: int,
    terms_id: int,
    payload: TermsDecisionRequest,
    request: Request,
    author_user: User = Depends(require_roles(UserRole.AUTHOR)),
    db: Session = Depends(get_db),
) -> WorkResponse:
    work = _load_work(db, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Eser bulunamadı.")

    if not any(link.author_id == author_user.id for link in work.author_links):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bu eserin yazarı olmadığınız için işlem yapamazsınız.",
        )

    terms = next((t for t in work.terms_versions if t.id == terms_id), None)
    if terms is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Koşul sürümü bulunamadı.")

    if terms.status != TermsStatus.PROPOSED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Bu koşul sürümü artık onay beklemiyor (mevcut durum: {terms.status.value}).",
        )

    existing_approval = db.scalar(
        select(TermsApproval).where(
            TermsApproval.terms_id == terms.id,
            TermsApproval.author_id == author_user.id,
        )
    )
    if existing_approval is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bu koşul sürümü için kararınızı zaten bildirdiniz.",
        )

    old_work_status = work.status.value
    old_terms_status = terms.status.value

    db.add(
        TermsApproval(
            terms_id=terms.id,
            author_id=author_user.id,
            decision=payload.decision,
            comment=payload.comment,
        )
    )
    db.flush()

    if payload.decision == ApprovalDecision.REVISION_REQUESTED:
        terms.status = TermsStatus.REVISION_REQUESTED
        terms.revision_note = payload.comment
        work.status = WorkStatus.PENDING_APPROVAL
        action_name = "work.terms_revision_requested"
    else:
        approved_author_ids = {
            a.author_id
            for a in db.scalars(
                select(TermsApproval).where(
                    TermsApproval.terms_id == terms.id,
                    TermsApproval.decision == ApprovalDecision.APPROVED,
                )
            ).all()
        }
        required_author_ids = {link.author_id for link in work.author_links}
        pending_invite_count = db.scalar(
            select(func.count(Invitation.id)).where(
                Invitation.work_id == work.id,
                Invitation.status == InvitationStatus.PENDING,
            )
        )

        # BFR-12: Tüm yazarlar onayladığında ve bekleyen davet kalmadığında kesinleşir
        if required_author_ids.issubset(approved_author_ids) and (pending_invite_count or 0) == 0:
            terms.status = TermsStatus.APPROVED
            work.status = (
                WorkStatus.UNAVAILABLE
                if terms.permission_level == PermissionLevel.UNAVAILABLE
                else WorkStatus.LICENSABLE
            )
        action_name = "work.terms_approved"

    record_audit(
        db,
        actor_user_id=author_user.id,
        action=action_name,
        entity_type="license_terms",
        entity_id=terms.id,
        old_value={"terms_status": old_terms_status, "work_status": old_work_status},
        new_value={
            "terms_status": terms.status.value,
            "work_status": work.status.value,
            "comment": payload.comment,
        },
        ip_address=get_client_ip(request),
    )
    db.commit()

    if work.publisher:
        send_transactional_email(
            to_email=work.publisher.corporate_email,
            subject=f"Yazar kararı ({payload.decision.value}): {work.title}",
            body=(
                f"Yazar {author_user.first_name} {author_user.last_name}, '{work.title}' eseri için "
                f"v{terms.version_no} teklifinde '{payload.decision.value}' kararı verdi."
            ),
        )

    loaded = _load_work(db, work.id)
    assert loaded is not None
    return _serialize_work(db, loaded)


@router.post(
    "/{work_id}/mark-unavailable",
    response_model=WorkResponse,
    summary="Yazar eserin izin seviyesini tek tıkla 'kullanılamaz' yapar (UC8 / BFR-10)",
)
def mark_work_unavailable(
    work_id: int,
    payload: MarkUnavailableRequest,
    request: Request,
    author_user: User = Depends(require_roles(UserRole.AUTHOR)),
    db: Session = Depends(get_db),
) -> WorkResponse:
    work = _load_work(db, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Eser bulunamadı.")

    if not any(link.author_id == author_user.id for link in work.author_links):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bu eserin yazarı olmadığınız için izin seviyesini değiştiremezsiniz.",
        )

    old_work_status = work.status.value
    terms_sorted = sorted(work.terms_versions, key=lambda t: t.version_no)
    last_terms = terms_sorted[-1] if terms_sorted else None
    next_version = (last_terms.version_no + 1) if last_terms else 1

    for t in terms_sorted:
        if t.status in (TermsStatus.PROPOSED, TermsStatus.REVISION_REQUESTED, TermsStatus.APPROVED):
            t.status = TermsStatus.SUPERSEDED
    db.flush()

    unavailable_terms = LicenseTerms(
        work_id=work.id,
        version_no=next_version,
        permission_level=PermissionLevel.UNAVAILABLE,
        fee_amount=last_terms.fee_amount if last_terms else Decimal("0.00"),
        currency=last_terms.currency if last_terms else "TRY",
        duration_months=last_terms.duration_months if last_terms else 36,
        author_share_pct=last_terms.author_share_pct if last_terms else Decimal("60.00"),
        publisher_share_pct=last_terms.publisher_share_pct if last_terms else Decimal("40.00"),
        status=TermsStatus.APPROVED,
        proposed_by_user_id=author_user.id,
        revision_note=payload.reason or "Yazar tarafından kullanılamaz olarak işaretlendi.",
    )
    db.add(unavailable_terms)
    work.status = WorkStatus.UNAVAILABLE
    db.flush()

    record_audit(
        db,
        actor_user_id=author_user.id,
        action="work.marked_unavailable",
        entity_type="work",
        entity_id=work.id,
        old_value={"work_status": old_work_status},
        new_value={
            "work_status": work.status.value,
            "terms_version": unavailable_terms.version_no,
            "reason": unavailable_terms.revision_note,
        },
        ip_address=get_client_ip(request),
    )
    db.commit()

    loaded = _load_work(db, work.id)
    assert loaded is not None
    return _serialize_work(db, loaded)


@router.post(
    "/{work_id}/invitations",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Eser için kayıtlı olmayan yazara e-posta daveti gönderir (SCRUM-46 / BFR-08)",
)
def invite_author_to_work(
    work_id: int,
    payload: AuthorInviteRequest,
    request: Request,
    publisher_user: User = Depends(require_approved_publisher),
    db: Session = Depends(get_db),
) -> Invitation:
    work = _load_work(db, work_id)
    if work is None or work.publisher_org_id != publisher_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Eser bulunamadı.")

    norm_email = payload.email.strip().lower()
    existing_pending = db.scalar(
        select(Invitation).where(
            Invitation.work_id == work.id,
            func.lower(Invitation.email) == norm_email,
            Invitation.status == InvitationStatus.PENDING,
        )
    )
    if existing_pending is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bu e-posta adresi için zaten bekleyen bir davet bulunuyor.",
        )

    inv, raw_token = _create_invitation(
        db,
        work=work,
        email=norm_email,
        split_percent=payload.split_percent,
        invited_by_user_id=publisher_user.id,
    )
    record_audit(
        db,
        actor_user_id=publisher_user.id,
        action="work.author_invited",
        entity_type="invitation",
        entity_id=inv.id,
        new_value={"work_id": work.id, "email": norm_email, "split_percent": str(payload.split_percent)},
        ip_address=get_client_ip(request),
    )
    db.commit()
    db.refresh(inv)
    _send_invitation_email(norm_email, work, raw_token)
    return inv