"""Kullanım raporu (UC15), işlem geçmişi (UC16) ve lisans raporu dışa aktarma (UC17)."""
import csv
from decimal import Decimal
import io

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, require_roles
from app.core.audit import get_client_ip, record_audit
from app.core.email import send_transactional_email
from app.database import get_db
from app.models.audit import AuditLog
from app.models.catalog import Work, WorkAuthor
from app.models.enums import PaymentStatus, RecipientType, RequestStatus, UserRole
from app.models.identity import User
from app.models.licensing import License, LicenseRequest, UsageReport
from app.models.payments import Payment
from app.schemas.reports import (
    AuditLogResponse,
    LicenseReportRow,
    UsageReportCreate,
    UsageReportResponse,
)

router = APIRouter(tags=["Raporlama ve Denetim"])


def _load_license(db: Session, license_id: int) -> License | None:
    stmt = (
        select(License)
        .where(License.id == license_id)
        .options(
            selectinload(License.request)
            .selectinload(LicenseRequest.work)
            .selectinload(Work.author_links)
            .selectinload(WorkAuthor.author),
            selectinload(License.request)
            .selectinload(LicenseRequest.work)
            .selectinload(Work.publisher),
            selectinload(License.usage_reports),
        )
        .execution_options(populate_existing=True)
    )
    return db.scalar(stmt)


def _ensure_license_visible(lic: License, user: User) -> None:
    req = lic.request
    if user.role == UserRole.ADMIN:
        return
    if user.role == UserRole.LICENSEE and req.licensee_user_id == user.id:
        return
    if user.role == UserRole.PUBLISHER_STAFF and req.work.publisher_org_id == user.organization_id:
        return
    if user.role == UserRole.AUTHOR and any(
        link.author_id == user.id for link in req.work.author_links
    ):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Bu lisansın kullanım raporlarını görüntüleme yetkiniz bulunmamaktadır.",
    )


@router.post(
    "/api/licenses/{license_id}/usage-reports",
    response_model=UsageReportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Aktif lisans için yapay zekâ kullanım raporu bildirir (UC15 / SCRUM-60)",
)
def submit_usage_report(
    license_id: int,
    payload: UsageReportCreate,
    request: Request,
    licensee_user: User = Depends(require_roles(UserRole.LICENSEE)),
    db: Session = Depends(get_db),
) -> UsageReport:
    lic = _load_license(db, license_id)
    if lic is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lisans bulunamadı.")

    if lic.request.licensee_user_id != licensee_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Yalnızca kendi lisansınız için kullanım raporu bildirebilirsiniz.",
        )

    # BFR-27: Yalnızca 'active' durumdaki lisans için kullanım raporu bildirilebilir
    if lic.request.status != RequestStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Kullanım raporu yalnızca 'active' durumundaki lisanslar için bildirilebilir "
                f"(mevcut durum: {lic.request.status.value})."
            ),
        )

    report = UsageReport(
        license_id=lic.id,
        reported_by_user_id=licensee_user.id,
        usage_type=payload.usage_type,
        period_start=payload.period_start,
        period_end=payload.period_end,
        model_name=payload.model_name.strip() if payload.model_name else None,
        description=payload.description.strip(),
        declaration_accepted=payload.declaration_accepted,
    )
    db.add(report)
    db.flush()

    record_audit(
        db,
        actor_user_id=licensee_user.id,
        action="usage_report.submitted",
        entity_type="usage_report",
        entity_id=report.id,
        new_value={
            "license_id": lic.id,
            "license_number": lic.license_number,
            "usage_type": payload.usage_type.value,
            "period_start": str(payload.period_start),
            "period_end": str(payload.period_end),
            "model_name": report.model_name,
        },
        ip_address=get_client_ip(request),
    )
    db.commit()
    db.refresh(report)

    work = lic.request.work
    if work.publisher:
        send_transactional_email(
            to_email=work.publisher.corporate_email,
            subject=f"Yeni kullanım raporu: {lic.license_number}",
            body=f"'{work.title}' eseri ({lic.license_number}) için yeni bir kullanım beyanı girildi.",
        )
    for link in work.author_links:
        send_transactional_email(
            to_email=link.author.email,
            subject=f"Yeni kullanım raporu: {lic.license_number}",
            body=f"'{work.title}' eseri ({lic.license_number}) için yeni bir kullanım beyanı girildi.",
        )

    return report


@router.get(
    "/api/licenses/{license_id}/usage-reports",
    response_model=list[UsageReportResponse],
    summary="Bir lisansa ait kullanım raporlarını rol bazlı listeler (UC15)",
)
def list_usage_reports(
    license_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[UsageReport]:
    lic = _load_license(db, license_id)
    if lic is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lisans bulunamadı.")
    _ensure_license_visible(lic, current_user)
    return sorted(lic.usage_reports, key=lambda r: r.reported_at, reverse=True)


@router.get(
    "/api/audit-logs",
    response_model=list[AuditLogResponse],
    summary="Değiştirilemez işlem geçmişini rol bazlı listeler (UC16 / BFR-28, BFR-29)",
)
def list_audit_logs(
    action: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AuditLog]:
    stmt = select(AuditLog)

    # BFR-29: Sistem Yöneticisi tüm kayıtları, diğer roller yalnızca kendi işlemlerini görür
    if current_user.role != UserRole.ADMIN:
        stmt = stmt.where(AuditLog.actor_user_id == current_user.id)

    if action:
        stmt = stmt.where(AuditLog.action == action)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)

    stmt = stmt.order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc()).offset(offset).limit(limit)
    return list(db.scalars(stmt).all())


def _build_license_report_rows(
    db: Session,
    *,
    current_user: User,
    req_status: RequestStatus | None = None,
) -> list[LicenseReportRow]:
    stmt = select(LicenseRequest).options(
        selectinload(LicenseRequest.work).selectinload(Work.author_links),
        selectinload(LicenseRequest.terms),
        selectinload(LicenseRequest.license).selectinload(License.usage_reports),
        selectinload(LicenseRequest.payments).selectinload(Payment.revenue_shares),
    )

    if current_user.role == UserRole.PUBLISHER_STAFF:
        stmt = stmt.join(Work, Work.id == LicenseRequest.work_id).where(
            Work.publisher_org_id == current_user.organization_id
        )
    elif current_user.role == UserRole.AUTHOR:
        stmt = stmt.join(WorkAuthor, WorkAuthor.work_id == LicenseRequest.work_id).where(
            WorkAuthor.author_id == current_user.id
        )

    if req_status is not None:
        stmt = stmt.where(LicenseRequest.status == req_status)

    requests_list = list(
        db.scalars(
            stmt.order_by(LicenseRequest.created_at.desc()).execution_options(populate_existing=True)
        ).all()
    )

    rows: list[LicenseReportRow] = []
    for req in requests_list:
        my_share = Decimal("0.00")
        platform_fee = Decimal("0.00")

        for payment in req.payments:
            if payment.status != PaymentStatus.SUCCEEDED:
                continue
            for rs in payment.revenue_shares:
                if rs.recipient_type == RecipientType.PLATFORM:
                    platform_fee += rs.amount
                if (
                    current_user.role == UserRole.AUTHOR
                    and rs.recipient_type == RecipientType.AUTHOR
                    and rs.recipient_user_id == current_user.id
                ):
                    my_share += rs.amount
                elif (
                    current_user.role == UserRole.PUBLISHER_STAFF
                    and rs.recipient_type == RecipientType.PUBLISHER
                    and rs.recipient_org_id == current_user.organization_id
                ):
                    my_share += rs.amount
                elif (
                    current_user.role == UserRole.ADMIN
                    and rs.recipient_type == RecipientType.PLATFORM
                ):
                    my_share += rs.amount

        rows.append(
            LicenseReportRow(
                request_id=req.id,
                license_number=req.license.license_number if req.license else None,
                work_isbn=req.work.isbn,
                work_title=req.work.title,
                status=req.status,
                intended_use=req.intended_use,
                fee_amount=req.terms.fee_amount,
                currency=req.terms.currency,
                starts_on=req.license.starts_on if req.license else None,
                ends_on=req.license.ends_on if req.license else None,
                my_revenue_share=my_share,
                platform_commission=platform_fee,
                usage_report_count=len(req.license.usage_reports) if req.license else 0,
                created_at=req.created_at,
            )
        )
    return rows


@router.get(
    "/api/reports/licenses",
    response_model=list[LicenseReportRow],
    summary="Yazar veya Yayınevi için lisans raporunu JSON olarak döndürür (UC17)",
)
def get_license_report(
    req_status: RequestStatus | None = Query(default=None, alias="status"),
    current_user: User = Depends(
        require_roles(UserRole.AUTHOR, UserRole.PUBLISHER_STAFF, UserRole.ADMIN)
    ),
    db: Session = Depends(get_db),
) -> list[LicenseReportRow]:
    return _build_license_report_rows(db, current_user=current_user, req_status=req_status)


@router.get(
    "/api/reports/licenses/export",
    summary="Lisans raporunu Excel uyumlu UTF-8 CSV dosyası olarak dışa aktarır (UC17 / SCRUM-69)",
)
def export_license_report_csv(
    req_status: RequestStatus | None = Query(default=None, alias="status"),
    current_user: User = Depends(
        require_roles(UserRole.AUTHOR, UserRole.PUBLISHER_STAFF, UserRole.ADMIN)
    ),
    db: Session = Depends(get_db),
) -> Response:
    rows = _build_license_report_rows(db, current_user=current_user, req_status=req_status)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "request_id",
            "license_number",
            "work_isbn",
            "work_title",
            "status",
            "intended_use",
            "fee_amount",
            "currency",
            "starts_on",
            "ends_on",
            "my_revenue_share",
            "platform_commission",
            "usage_report_count",
            "created_at",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row.request_id,
                row.license_number or "",
                row.work_isbn,
                row.work_title,
                row.status.value,
                row.intended_use.value,
                f"{row.fee_amount:.2f}",
                row.currency,
                str(row.starts_on) if row.starts_on else "",
                str(row.ends_on) if row.ends_on else "",
                f"{row.my_revenue_share:.2f}",
                f"{row.platform_commission:.2f}",
                row.usage_report_count,
                row.created_at.isoformat(),
            ]
        )

    # UTF-8 BOM: Türkçe karakterlerin Microsoft Excel'de bozulmadan açılması için
    csv_bytes = ("\ufeff" + buffer.getvalue()).encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="eserlisans_lisans_raporu.csv"'},
    )