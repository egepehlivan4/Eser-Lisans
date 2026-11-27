"""Lisans talebi, çift onay, sözleşme kabulü, ödeme, gelir paylaşımı ve süre dolumu (Sprint 4)."""
import calendar
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, require_roles
from app.core.audit import get_client_ip, record_audit
from app.core.email import send_transactional_email
from app.database import get_db
from app.models.catalog import LicenseTerms, Work, WorkAuthor
from app.models.enums import (
    ApprovalParty,
    IntendedUse,
    PaymentStatus,
    PermissionLevel,
    RecipientType,
    RequestDecision,
    RequestStatus,
    TermsStatus,
    UserRole,
    WorkStatus,
)
from app.models.identity import User
from app.models.licensing import License, LicenseRequest, RequestApproval
from app.models.payments import Payment, RevenueShare
from app.schemas.licensing import (
    ContractAcceptRequest,
    ExpireLicensesResponse,
    LicenseRequestCreate,
    LicenseRequestDecisionInput,
    LicenseRequestResponse,
    LicenseResponse,
    PaymentAttemptResponse,
    PaymentExecuteRequest,
    PaymentResponse,
    RequestApprovalResponse,
    RevenueShareResponse,
    RevenueSummaryResponse,
)
from app.services.payment_service import get_payment_adapter
from app.services.revenue_service import calculate_revenue_shares

router = APIRouter(tags=["Lisanslama ve Ödeme"])

# Lisans durum makinesi (LisansDurumDiyagrami.png & license_requests_guard — BFR-16)
ALLOWED_STATUS_TRANSITIONS: dict[RequestStatus, set[RequestStatus]] = {
    RequestStatus.REQUESTED: {RequestStatus.AWAITING_APPROVAL, RequestStatus.REJECTED},
    RequestStatus.AWAITING_APPROVAL: {RequestStatus.APPROVED, RequestStatus.REJECTED},
    RequestStatus.APPROVED: {RequestStatus.AWAITING_PAYMENT, RequestStatus.ACTIVE},
    RequestStatus.AWAITING_PAYMENT: {RequestStatus.ACTIVE},
    RequestStatus.ACTIVE: {RequestStatus.EXPIRED},
    RequestStatus.EXPIRED: set(),
    RequestStatus.REJECTED: set(),
}


def transition_request_status(license_req: LicenseRequest, target: RequestStatus) -> None:
    """Tanımlı olmayan her durum geçişini reddeder (BFR-16)."""
    allowed = ALLOWED_STATUS_TRANSITIONS.get(license_req.status, set())
    if target not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Geçersiz lisans durum geçişi: {license_req.status.value} -> {target.value}",
        )
    license_req.status = target


def _add_months(start_date: date, months: int) -> date:
    month_idx = start_date.month - 1 + months
    year = start_date.year + month_idx // 12
    month = month_idx % 12 + 1
    day = min(start_date.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _generate_license_number(request_id: int) -> str:
    year = datetime.now(timezone.utc).year
    return f"EL-{year}-{request_id:05d}-{secrets.token_hex(2).upper()}"


def _load_request(db: Session, request_id: int, *, lock: bool = False) -> LicenseRequest | None:
    if lock and db.bind is not None and db.bind.dialect.name == "postgresql":
        # Eşzamanlı onay/ödeme yarışlarını önlemek için satır kilidi (BFR-18)
        db.execute(select(LicenseRequest.id).where(LicenseRequest.id == request_id).with_for_update())

    stmt = (
        select(LicenseRequest)
        .where(LicenseRequest.id == request_id)
        .options(
            selectinload(LicenseRequest.work)
            .selectinload(Work.author_links)
            .selectinload(WorkAuthor.author),
            selectinload(LicenseRequest.work).selectinload(Work.publisher),
            selectinload(LicenseRequest.terms),
            selectinload(LicenseRequest.licensee_user),
            selectinload(LicenseRequest.approvals),
            selectinload(LicenseRequest.license),
            selectinload(LicenseRequest.payments).selectinload(Payment.revenue_shares),
        )
        .execution_options(populate_existing=True)
    )
    return db.scalar(stmt)


def _serialize_request(req: LicenseRequest) -> LicenseRequestResponse:
    return LicenseRequestResponse(
        id=req.id,
        work_id=req.work_id,
        work_title=req.work.title if req.work else None,
        work_isbn=req.work.isbn if req.work else None,
        terms_id=req.terms_id,
        fee_amount=req.terms.fee_amount if req.terms else None,
        currency=req.terms.currency if req.terms else "TRY",
        duration_months=req.terms.duration_months if req.terms else None,
        licensee_user_id=req.licensee_user_id,
        licensee_org_id=req.licensee_org_id,
        intended_use=req.intended_use,
        purpose_description=req.purpose_description,
        status=req.status,
        rejection_reason=req.rejection_reason,
        created_at=req.created_at,
        updated_at=req.updated_at,
        approvals=[RequestApprovalResponse.model_validate(a) for a in req.approvals],
        license=LicenseResponse.model_validate(req.license) if req.license else None,
        payments=[PaymentResponse.model_validate(p) for p in req.payments],
    )


def _ensure_request_visible(req: LicenseRequest, user: User) -> None:
    if user.role == UserRole.ADMIN:
        return
    if user.role == UserRole.LICENSEE and req.licensee_user_id == user.id:
        return
    if user.role == UserRole.PUBLISHER_STAFF and req.work.publisher_org_id == user.organization_id:
        return
    if user.role == UserRole.AUTHOR and any(link.author_id == user.id for link in req.work.author_links):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Bu lisans talebini görüntüleme yetkiniz bulunmamaktadır.",
    )


def _create_active_license(
    db: Session,
    *,
    license_req: LicenseRequest,
    contract_template_version: str = "v1.0",
) -> License:
    starts_on = date.today()
    ends_on = _add_months(starts_on, license_req.terms.duration_months)
    lic = License(
        request_id=license_req.id,
        license_number=_generate_license_number(license_req.id),
        contract_template_version=contract_template_version,
        is_sample_contract=True,  # Pilotta örnek sözleşme (FSEK md. 52 — R7)
        starts_on=starts_on,
        ends_on=ends_on,
    )
    db.add(lic)
    db.flush()
    return lic


@router.post(
    "/api/license-requests",
    response_model=LicenseRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Lisanslanabilir eser için yeni lisans talebi oluşturur (UC10 / SCRUM-50)",
)
def create_license_request(
    payload: LicenseRequestCreate,
    request: Request,
    licensee_user: User = Depends(require_roles(UserRole.LICENSEE)),
    db: Session = Depends(get_db),
) -> LicenseRequestResponse:
    work = db.scalar(
        select(Work)
        .where(Work.id == payload.work_id)
        .options(
            selectinload(Work.publisher),
            selectinload(Work.author_links).selectinload(WorkAuthor.author),
            selectinload(Work.terms_versions),
        )
        .execution_options(populate_existing=True)
    )
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Eser bulunamadı.")

    if work.status != WorkStatus.LICENSABLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bu eser şu anda lisanslanabilir durumda değildir.",
        )

    approved_terms = next(
        (t for t in work.terms_versions if t.status == TermsStatus.APPROVED), None
    )
    if approved_terms is None or approved_terms.permission_level == PermissionLevel.UNAVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Eserin onaylı ve lisanslamaya açık bir koşul sürümü bulunmamaktadır.",
        )

    # BFR-15: Ticari amaçlı talep, yalnızca akademik/ticari olmayan izinli eserde reddedilir
    if (
        payload.intended_use == IntendedUse.COMMERCIAL
        and approved_terms.permission_level == PermissionLevel.ACADEMIC_ONLY
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Bu eser yalnızca akademik / ticari olmayan yapay zekâ eğitimine açıktır.",
        )

    license_req = LicenseRequest(
        work_id=work.id,
        terms_id=approved_terms.id,
        licensee_user_id=licensee_user.id,
        licensee_org_id=licensee_user.organization_id,
        intended_use=payload.intended_use,
        purpose_description=payload.purpose_description.strip(),
        status=RequestStatus.REQUESTED,
    )
    db.add(license_req)
    db.flush()

    # Durum diyagramı: Talep Edildi --sistem doğrular--> Hak Sahipleri Onayı Bekleniyor
    transition_request_status(license_req, RequestStatus.AWAITING_APPROVAL)
    db.flush()

    record_audit(
        db,
        actor_user_id=licensee_user.id,
        action="license_request.created",
        entity_type="license_request",
        entity_id=license_req.id,
        new_value={
            "work_id": work.id,
            "terms_id": approved_terms.id,
            "intended_use": payload.intended_use.value,
            "status": license_req.status.value,
        },
        ip_address=get_client_ip(request),
    )
    db.commit()

    # Hak sahiplerine bildirim (BFR-31)
    if work.publisher:
        send_transactional_email(
            to_email=work.publisher.corporate_email,
            subject=f"Yeni lisans talebi (#{license_req.id}): {work.title}",
            body=f"'{work.title}' eseri için yeni bir lisans talebi oluşturuldu. Panelinizden değerlendirebilirsiniz.",
        )
    for link in work.author_links:
        send_transactional_email(
            to_email=link.author.email,
            subject=f"Yeni lisans talebi (#{license_req.id}): {work.title}",
            body=f"'{work.title}' eseri için yeni bir lisans talebi oluşturuldu. Panelinizden değerlendirebilirsiniz.",
        )

    loaded = _load_request(db, license_req.id)
    assert loaded is not None
    return _serialize_request(loaded)


@router.get(
    "/api/license-requests",
    response_model=list[LicenseRequestResponse],
    summary="Kullanıcının rolüne göre ilgili lisans taleplerini listeler (UC14 / BFR-20)",
)
def list_license_requests(
    req_status: RequestStatus | None = Query(default=None, alias="status"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[LicenseRequestResponse]:
    stmt = select(LicenseRequest).options(
        selectinload(LicenseRequest.work).selectinload(Work.author_links),
        selectinload(LicenseRequest.terms),
        selectinload(LicenseRequest.approvals),
        selectinload(LicenseRequest.license),
        selectinload(LicenseRequest.payments).selectinload(Payment.revenue_shares),
    )

    if current_user.role == UserRole.LICENSEE:
        stmt = stmt.where(LicenseRequest.licensee_user_id == current_user.id)
    elif current_user.role == UserRole.PUBLISHER_STAFF:
        stmt = stmt.join(Work, Work.id == LicenseRequest.work_id).where(
            Work.publisher_org_id == current_user.organization_id
        )
    elif current_user.role == UserRole.AUTHOR:
        stmt = stmt.join(WorkAuthor, WorkAuthor.work_id == LicenseRequest.work_id).where(
            WorkAuthor.author_id == current_user.id
        )

    if req_status is not None:
        stmt = stmt.where(LicenseRequest.status == req_status)

    items = list(
        db.scalars(
            stmt.order_by(LicenseRequest.created_at.desc()).execution_options(populate_existing=True)
        ).all()
    )
    return [_serialize_request(r) for r in items]


@router.get(
    "/api/license-requests/{request_id}",
    response_model=LicenseRequestResponse,
    summary="Lisans talebi ve varsa aktif lisans bilgisini döndürür (UC14)",
)
def get_license_request(
    request_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LicenseRequestResponse:
    license_req = _load_request(db, request_id)
    if license_req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lisans talebi bulunamadı.")
    _ensure_request_visible(license_req, current_user)
    return _serialize_request(license_req)


@router.post(
    "/api/license-requests/{request_id}/decision",
    response_model=LicenseRequestResponse,
    summary="Yazar veya Yayınevi lisans talebini onaylar veya reddeder (UC11 / SCRUM-50)",
)
def decide_license_request(
    request_id: int,
    payload: LicenseRequestDecisionInput,
    request: Request,
    current_user: User = Depends(require_roles(UserRole.AUTHOR, UserRole.PUBLISHER_STAFF)),
    db: Session = Depends(get_db),
) -> LicenseRequestResponse:
    license_req = _load_request(db, request_id, lock=True)
    if license_req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lisans talebi bulunamadı.")

    if license_req.status != RequestStatus.AWAITING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Bu talep değerlendirme aşamasında değildir (mevcut durum: {license_req.status.value}).",
        )

    # Kullanıcının tarafını doğrula
    if current_user.role == UserRole.AUTHOR:
        if not any(link.author_id == current_user.id for link in license_req.work.author_links):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bu eserin yazarı değilsiniz.")
        party = ApprovalParty.AUTHOR
    else:
        if license_req.work.publisher_org_id != current_user.organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Bu eserin yayınevi yetkilisi değilsiniz."
            )
        party = ApprovalParty.PUBLISHER

    # BFR-18: Tekrarlı onay/ret kontrolü
    if any(a.user_id == current_user.id for a in license_req.approvals):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bu lisans talebi için kararınızı zaten bildirdiniz.",
        )
    if party == ApprovalParty.PUBLISHER and any(
        a.party == ApprovalParty.PUBLISHER for a in license_req.approvals
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Yayınevi tarafı bu talep için kararını zaten bildirdi.",
        )

    old_status = license_req.status.value
    db.add(
        RequestApproval(
            request_id=license_req.id,
            party=party,
            user_id=current_user.id,
            decision=payload.decision,
            comment=payload.comment,
        )
    )
    db.flush()

    if payload.decision == RequestDecision.REJECTED:
        transition_request_status(license_req, RequestStatus.REJECTED)
        license_req.rejection_reason = payload.comment
    else:
        all_approvals = list(
            db.scalars(
                select(RequestApproval).where(
                    RequestApproval.request_id == license_req.id,
                    RequestApproval.decision == RequestDecision.APPROVED,
                )
            ).all()
        )
        publisher_approved = any(a.party == ApprovalParty.PUBLISHER for a in all_approvals)
        approved_author_ids = {a.user_id for a in all_approvals if a.party == ApprovalParty.AUTHOR}
        required_author_ids = {link.author_id for link in license_req.work.author_links}

        # BFR-17: Hem yayınevi hem tüm yazarlar onayladığında "approved" olur
        if publisher_approved and required_author_ids.issubset(approved_author_ids):
            transition_request_status(license_req, RequestStatus.APPROVED)

    record_audit(
        db,
        actor_user_id=current_user.id,
        action=f"license_request.{party.value}_{payload.decision.value}",
        entity_type="license_request",
        entity_id=license_req.id,
        old_value={"status": old_status},
        new_value={"status": license_req.status.value, "comment": payload.comment},
        ip_address=get_client_ip(request),
    )
    db.commit()

    if license_req.status in (RequestStatus.APPROVED, RequestStatus.REJECTED):
        send_transactional_email(
            to_email=license_req.licensee_user.email,
            subject=f"Lisans talebiniz sonuçlandı ({license_req.status.value})",
            body=(
                f"'{license_req.work.title}' eseri için #{license_req.id} numaralı talebiniz "
                f"'{license_req.status.value}' durumuna geçti."
            ),
        )

    loaded = _load_request(db, license_req.id)
    assert loaded is not None
    return _serialize_request(loaded)


@router.post(
    "/api/license-requests/{request_id}/accept-contract",
    response_model=LicenseRequestResponse,
    summary="Lisans Alıcısı örnek sözleşmeyi kabul eder; ücretsizse aktif, ücretliyse ödeme bekleniyor olur (SCRUM-51)",
)
def accept_license_contract(
    request_id: int,
    payload: ContractAcceptRequest,
    request: Request,
    licensee_user: User = Depends(require_roles(UserRole.LICENSEE)),
    db: Session = Depends(get_db),
) -> LicenseRequestResponse:
    license_req = _load_request(db, request_id, lock=True)
    if license_req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lisans talebi bulunamadı.")
    if license_req.licensee_user_id != licensee_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Bu lisans talebi size ait değildir."
        )

    old_status = license_req.status.value
    fee = license_req.terms.fee_amount

    if fee == Decimal("0"):
        # karar_ve_kurallar.md: Ücretsiz lisansta ödeme kaydı açılmaz, doğrudan active olur
        transition_request_status(license_req, RequestStatus.ACTIVE)
        lic = _create_active_license(
            db,
            license_req=license_req,
            contract_template_version=payload.contract_template_version,
        )
        audit_new = {
            "status": license_req.status.value,
            "license_number": lic.license_number,
            "free_license": True,
        }
    else:
        transition_request_status(license_req, RequestStatus.AWAITING_PAYMENT)
        audit_new = {
            "status": license_req.status.value,
            "fee_amount": str(fee),
            "contract_template_version": payload.contract_template_version,
        }

    record_audit(
        db,
        actor_user_id=licensee_user.id,
        action="license_request.contract_accepted",
        entity_type="license_request",
        entity_id=license_req.id,
        old_value={"status": old_status},
        new_value=audit_new,
        ip_address=get_client_ip(request),
    )
    db.commit()

    loaded = _load_request(db, license_req.id)
    assert loaded is not None
    return _serialize_request(loaded)


@router.post(
    "/api/license-requests/{request_id}/pay",
    response_model=PaymentAttemptResponse,
    summary="Lisans bedelini ödeme adaptörü üzerinden öder, gelir paylaşımını ve lisansı tek ACID işlemde yazar (UC12 / SCRUM-90)",
)
def pay_license_request(
    request_id: int,
    payload: PaymentExecuteRequest,
    request: Request,
    licensee_user: User = Depends(require_roles(UserRole.LICENSEE)),
    db: Session = Depends(get_db),
) -> PaymentAttemptResponse:
    license_req = _load_request(db, request_id, lock=True)
    if license_req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lisans talebi bulunamadı.")
    if license_req.licensee_user_id != licensee_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Bu lisans talebi size ait değildir."
        )

    # BFR-24: İdempotency kontrolü — aynı anahtarla gelen ikinci istek tekrar işlenmez
    existing_payment = db.scalar(
        select(Payment)
        .where(Payment.idempotency_key == payload.idempotency_key)
        .options(selectinload(Payment.revenue_shares))
        .execution_options(populate_existing=True)
    )
    if existing_payment is not None:
        if existing_payment.request_id != license_req.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Bu idempotency anahtarı başka bir lisans talebi için kullanılmıştır.",
            )
        return PaymentAttemptResponse(
            payment=PaymentResponse.model_validate(existing_payment),
            request_status=license_req.status,
            license=LicenseResponse.model_validate(license_req.license) if license_req.license else None,
            idempotent_replay=True,
        )

    if license_req.status != RequestStatus.AWAITING_PAYMENT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ödeme yapılabilmesi için talep 'awaiting_payment' durumunda olmalıdır (mevcut: {license_req.status.value}).",
        )

    amount = license_req.terms.fee_amount
    if amount <= Decimal("0"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ücretsiz lisanslar için ödeme işlemi yapılmaz.",
        )

    adapter = get_payment_adapter(payload.provider)
    charge_result = adapter.charge(
        request_id=license_req.id,
        amount=amount,
        currency=license_req.terms.currency,
        idempotency_key=payload.idempotency_key,
        simulation_scenario=payload.simulation_scenario,
    )

    now = datetime.now(timezone.utc)
    if not charge_result.succeeded:
        # BFR-22: Başarısız ödeme -> lisans aktif olmaz, neden kaydedilir, durum awaiting_payment kalır
        failed_payment = Payment(
            request_id=license_req.id,
            amount=amount,
            currency=license_req.terms.currency,
            status=PaymentStatus.FAILED,
            provider=payload.provider,
            provider_reference=charge_result.provider_reference,
            failure_reason=charge_result.failure_reason,
            idempotency_key=payload.idempotency_key,
            completed_at=now,
        )
        db.add(failed_payment)
        db.flush()

        record_audit(
            db,
            actor_user_id=licensee_user.id,
            action="payment.failed",
            entity_type="payment",
            entity_id=failed_payment.id,
            new_value={
                "request_id": license_req.id,
                "amount": str(amount),
                "failure_reason": charge_result.failure_reason.value if charge_result.failure_reason else None,
            },
            ip_address=get_client_ip(request),
        )
        db.commit()
        db.refresh(failed_payment)
        return PaymentAttemptResponse(
            payment=PaymentResponse.model_validate(failed_payment),
            request_status=license_req.status,
            license=None,
            idempotent_replay=False,
            error_message=charge_result.error_message,
        )

    # BFR-23: Tek ACID veritabanı işleminde Payment + RevenueShare + License + Active geçişi
    try:
        succeeded_payment = Payment(
            request_id=license_req.id,
            amount=amount,
            currency=license_req.terms.currency,
            status=PaymentStatus.SUCCEEDED,
            provider=payload.provider,
            provider_reference=charge_result.provider_reference,
            failure_reason=None,
            idempotency_key=payload.idempotency_key,
            completed_at=now,
        )
        db.add(succeeded_payment)
        db.flush()

        breakdown = calculate_revenue_shares(
            total_amount=amount,
            author_share_pct=license_req.terms.author_share_pct,
            publisher_share_pct=license_req.terms.publisher_share_pct,
            author_splits=[
                (link.author_id, link.split_percent) for link in license_req.work.author_links
            ],
        )

        # Platform komisyonu (%15)
        db.add(
            RevenueShare(
                payment_id=succeeded_payment.id,
                recipient_type=RecipientType.PLATFORM,
                recipient_user_id=None,
                recipient_org_id=None,
                share_percent=breakdown.platform_share_percent,
                amount=breakdown.platform_amount,
            )
        )
        # Yayınevi payı
        db.add(
            RevenueShare(
                payment_id=succeeded_payment.id,
                recipient_type=RecipientType.PUBLISHER,
                recipient_user_id=None,
                recipient_org_id=license_req.work.publisher_org_id,
                share_percent=breakdown.publisher_share_percent,
                amount=breakdown.publisher_amount,
            )
        )
        # Yazar pay(lar)ı
        for alloc in breakdown.author_allocations:
            db.add(
                RevenueShare(
                    payment_id=succeeded_payment.id,
                    recipient_type=RecipientType.AUTHOR,
                    recipient_user_id=alloc.author_id,
                    recipient_org_id=None,
                    share_percent=alloc.effective_share_percent,
                    amount=alloc.amount,
                )
            )

        old_status = license_req.status.value
        transition_request_status(license_req, RequestStatus.ACTIVE)
        lic = _create_active_license(db, license_req=license_req)

        record_audit(
            db,
            actor_user_id=licensee_user.id,
            action="payment.succeeded",
            entity_type="payment",
            entity_id=succeeded_payment.id,
            old_value={"request_status": old_status},
            new_value={
                "request_id": license_req.id,
                "request_status": license_req.status.value,
                "license_number": lic.license_number,
                "amount": str(amount),
                "platform_commission": str(breakdown.platform_amount),
                "publisher_amount": str(breakdown.publisher_amount),
            },
            ip_address=get_client_ip(request),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    reloaded_payment = db.scalar(
        select(Payment)
        .where(Payment.id == succeeded_payment.id)
        .options(selectinload(Payment.revenue_shares))
        .execution_options(populate_existing=True)
    )
    assert reloaded_payment is not None

    send_transactional_email(
        to_email=licensee_user.email,
        subject=f"Lisansınız aktifleşti: {lic.license_number}",
        body=f"'{license_req.work.title}' eseri için ödemeniz onaylandı. Lisans no: {lic.license_number}",
    )

    return PaymentAttemptResponse(
        payment=PaymentResponse.model_validate(reloaded_payment),
        request_status=license_req.status,
        license=LicenseResponse.model_validate(lic),
        idempotent_replay=False,
    )


@router.get(
    "/api/revenue-shares",
    response_model=RevenueSummaryResponse,
    summary="Yazar veya Yayınevi kendi gelir paylarını görüntüler (UC13 / SCRUM-91)",
)
def list_revenue_shares(
    current_user: User = Depends(
        require_roles(UserRole.AUTHOR, UserRole.PUBLISHER_STAFF, UserRole.ADMIN)
    ),
    db: Session = Depends(get_db),
) -> RevenueSummaryResponse:
    stmt = select(RevenueShare).order_by(RevenueShare.created_at.desc())

    if current_user.role == UserRole.AUTHOR:
        stmt = stmt.where(
            RevenueShare.recipient_type == RecipientType.AUTHOR,
            RevenueShare.recipient_user_id == current_user.id,
        )
    elif current_user.role == UserRole.PUBLISHER_STAFF:
        stmt = stmt.where(
            RevenueShare.recipient_type == RecipientType.PUBLISHER,
            RevenueShare.recipient_org_id == current_user.organization_id,
        )

    rows = list(db.scalars(stmt).all())
    total = sum((r.amount for r in rows), Decimal("0.00"))
    return RevenueSummaryResponse(
        total_amount=total,
        count=len(rows),
        items=[RevenueShareResponse.model_validate(r) for r in rows],
    )


def process_license_expirations(
    db: Session,
    *,
    as_of_date: date | None = None,
    reminder_days: int = 30,
    actor_user_id: int | None = None,
    ip_address: str | None = None,
) -> tuple[int, int]:
    """Süresi dolan aktif lisansları 'expired' yapar ve bitişi yaklaşanlara hatırlatma yollar (BFR-19)."""
    today = as_of_date or date.today()
    reminder_cutoff = today + timedelta(days=reminder_days)

    active_requests = list(
        db.scalars(
            select(LicenseRequest)
            .where(LicenseRequest.status == RequestStatus.ACTIVE)
            .options(
                selectinload(LicenseRequest.license),
                selectinload(LicenseRequest.licensee_user),
                selectinload(LicenseRequest.work),
            )
            .execution_options(populate_existing=True)
        ).all()
    )

    expired_count = 0
    reminded_count = 0
    emails_to_send: list[tuple[str, str, str]] = []

    for req in active_requests:
        if req.license is None:
            continue
        if req.license.ends_on < today:
            transition_request_status(req, RequestStatus.EXPIRED)
            record_audit(
                db,
                actor_user_id=actor_user_id,
                action="license.expired",
                entity_type="license_request",
                entity_id=req.id,
                old_value={"status": RequestStatus.ACTIVE.value},
                new_value={"status": RequestStatus.EXPIRED.value, "ends_on": str(req.license.ends_on)},
                ip_address=ip_address,
            )
            expired_count += 1
            emails_to_send.append(
                (
                    req.licensee_user.email,
                    f"Lisans süresi doldu: {req.license.license_number}",
                    f"'{req.work.title}' eseri için lisansınızın süresi {req.license.ends_on} tarihinde dolmuştur.",
                )
            )
        elif today <= req.license.ends_on <= reminder_cutoff:
            reminded_count += 1
            emails_to_send.append(
                (
                    req.licensee_user.email,
                    f"Lisans bitiş hatırlatması: {req.license.license_number}",
                    f"'{req.work.title}' eseri için lisansınız {req.license.ends_on} tarihinde sona erecektir.",
                )
            )

    if expired_count > 0:
        db.commit()

    for to_email, subject, body in emails_to_send:
        send_transactional_email(to_email=to_email, subject=subject, body=body)

    return expired_count, reminded_count


@router.post(
    "/api/licenses/expire-overdue",
    response_model=ExpireLicensesResponse,
    summary="Süresi dolan aktif lisansları 'expired' durumuna geçirir ve hatırlatma yollar (BFR-19)",
)
def trigger_license_expirations(
    request: Request,
    as_of: date | None = Query(default=None),
    admin_user: User = Depends(require_roles(UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> ExpireLicensesResponse:
    expired_count, reminded_count = process_license_expirations(
        db,
        as_of_date=as_of,
        actor_user_id=admin_user.id,
        ip_address=get_client_ip(request),
    )
    return ExpireLicensesResponse(expired_count=expired_count, reminded_count=reminded_count)