"""Sistem Yöneticisi uç noktaları: yayınevi hesap onayı (SCRUM-37: BFR-05)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.audit import get_client_ip, record_audit
from app.core.email import send_transactional_email
from app.database import get_db
from app.models.enums import ApprovalStatus, OrganizationType, UserRole
from app.models.identity import Organization, User
from app.schemas.auth import OrganizationResponse, OrganizationReviewRequest

router = APIRouter(prefix="/api/admin", tags=["Sistem Yönetimi"])


@router.get(
    "/publishers",
    response_model=list[OrganizationResponse],
    summary="Yayınevi hesaplarını durumuna göre listeler (UC3)",
)
def list_publishers(
    approval_status: ApprovalStatus | None = Query(default=None),
    _: User = Depends(require_roles(UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> list[Organization]:
    stmt = select(Organization).where(Organization.type == OrganizationType.PUBLISHER)
    if approval_status is not None:
        stmt = stmt.where(Organization.approval_status == approval_status)
    stmt = stmt.order_by(Organization.created_at.desc())
    return list(db.scalars(stmt).all())


@router.post(
    "/publishers/{org_id}/review",
    response_model=OrganizationResponse,
    summary="Yayınevi hesabını onaylar veya reddeder (UC3)",
)
def review_publisher(
    org_id: int,
    payload: OrganizationReviewRequest,
    request: Request,
    admin_user: User = Depends(require_roles(UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> Organization:
    org = db.get(Organization, org_id)
    if org is None or org.type != OrganizationType.PUBLISHER:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Yayınevi kurumu bulunamadı.",
        )

    old_status = org.approval_status.value
    org.approval_status = payload.decision
    org.reviewed_at = datetime.now(timezone.utc)
    org.rejection_reason = (
        payload.rejection_reason.strip()
        if payload.decision == ApprovalStatus.REJECTED and payload.rejection_reason
        else None
    )

    record_audit(
        db,
        actor_user_id=admin_user.id,
        action=f"publisher.{payload.decision.value}",
        entity_type="organization",
        entity_id=org.id,
        old_value={"approval_status": old_status},
        new_value={
            "approval_status": org.approval_status.value,
            "rejection_reason": org.rejection_reason,
        },
        ip_address=get_client_ip(request),
    )
    db.commit()
    db.refresh(org)

    send_transactional_email(
        to_email=org.corporate_email,
        subject=f"EserLisans yayınevi başvurusu: {org.approval_status.value}",
        body=(
            f"{org.name} yayınevi hesabınız onaylandı. Artık ISBN ile eser kaydedebilirsiniz."
            if org.approval_status == ApprovalStatus.APPROVED
            else f"{org.name} yayınevi hesabınız reddedildi. Gerekçe: {org.rejection_reason}"
        ),
    )
    return org