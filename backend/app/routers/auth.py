"""Kimlik ve yetkilendirme uç noktaları (SCRUM-36: BFR-01, BFR-02, BFR-03, BFR-04)."""
from datetime import datetime, timezone
import hashlib

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.audit import get_client_ip, record_audit
from app.core.email import send_transactional_email
from app.core.security import (
    create_access_token,
    generate_author_code,
    hash_password,
    login_rate_limiter,
    verify_password,
)
from app.database import get_db
from app.models.catalog import WorkAuthor
from app.models.enums import ApprovalStatus, InvitationStatus, OrganizationType, UserRole
from app.models.identity import Invitation, Organization, User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/api/auth", tags=["Kimlik ve Yetkilendirme"])


def _unique_author_code(db: Session) -> str:
    for _ in range(10):
        code = generate_author_code()
        exists = db.scalar(select(User.id).where(User.author_code == code))
        if exists is None:
            return code
    raise RuntimeError("Benzersiz yazar kodu üretilemedi.")


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Yeni kullanıcı hesabı oluşturur (UC1)",
)
def register(
    payload: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    normalized_email = payload.email.strip().lower()
    existing_user = db.scalar(select(User).where(func.lower(User.email) == normalized_email))
    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bu e-posta adresi ile kayıtlı bir hesap zaten mevcut.",
        )

    now = datetime.now(timezone.utc)
    org: Organization | None = None

    if payload.organization is not None:
        existing_org = db.scalar(
            select(Organization).where(
                Organization.tax_or_mersis_no == payload.organization.tax_or_mersis_no.strip()
            )
        )
        if existing_org is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Bu vergi/MERSİS numarası ile kayıtlı bir kurum zaten mevcut.",
            )

        org_type = (
            OrganizationType.PUBLISHER
            if payload.role == UserRole.PUBLISHER_STAFF
            else OrganizationType.LICENSEE
        )
        # BFR-05: Yayınevi hesabı "pending" açılır; lisans alıcısı kurumu doğrudan aktifleşir.
        approval_status = (
            ApprovalStatus.PENDING
            if org_type == OrganizationType.PUBLISHER
            else ApprovalStatus.APPROVED
        )
        org = Organization(
            type=org_type,
            name=payload.organization.name.strip(),
            tax_or_mersis_no=payload.organization.tax_or_mersis_no.strip(),
            corporate_email=payload.organization.corporate_email.strip().lower(),
            approval_status=approval_status,
            reviewed_at=now if approval_status == ApprovalStatus.APPROVED else None,
        )
        db.add(org)
        db.flush()

    author_code = _unique_author_code(db) if payload.role == UserRole.AUTHOR else None

    user = User(
        email=normalized_email,
        password_hash=hash_password(payload.password),
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip(),
        role=payload.role,
        organization_id=org.id if org else None,
        author_code=author_code,
        is_active=True,
        privacy_notice_accepted_at=now,
        pilot_consent_at=now if payload.pilot_consent_accepted else None,
    )
    db.add(user)
    db.flush()

    # Davetli yazar kaydoluyorsa bekleyen davetini otomatik kabul et ve esere bağla
    if payload.role == UserRole.AUTHOR and payload.invitation_token:
        token_hash = hashlib.sha256(payload.invitation_token.encode("utf-8")).hexdigest()
        invitation = db.scalar(
            select(Invitation).where(
                Invitation.token_hash == token_hash,
                Invitation.status == InvitationStatus.PENDING,
            )
        )
        if invitation is not None:
            if invitation.expires_at.replace(tzinfo=timezone.utc) < now:
                invitation.status = InvitationStatus.EXPIRED
            elif invitation.email.lower() == normalized_email:
                invitation.status = InvitationStatus.ACCEPTED
                invitation.accepted_user_id = user.id
                already_linked = db.get(WorkAuthor, (invitation.work_id, user.id))
                if already_linked is None:
                    db.add(
                        WorkAuthor(
                            work_id=invitation.work_id,
                            author_id=user.id,
                            split_percent=invitation.split_percent,
                        )
                    )

    record_audit(
        db,
        actor_user_id=user.id,
        action="user.registered",
        entity_type="user",
        entity_id=user.id,
        new_value={
            "role": user.role.value,
            "organization_id": user.organization_id,
            "pilot_consent": payload.pilot_consent_accepted,
        },
        ip_address=get_client_ip(request),
    )
    db.commit()
    db.refresh(user)

    send_transactional_email(
        to_email=user.email,
        subject="EserLisans hesabınız oluşturuldu",
        body=(
            f"Merhaba {user.first_name}, EserLisans hesabınız ({user.role.value}) oluşturuldu."
            + (
                " Yayınevi hesabınız Sistem Yöneticisi onayından sonra aktifleşecektir."
                if user.role == UserRole.PUBLISHER_STAFF
                else ""
            )
        ),
    )
    return user


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="E-posta ve şifre ile giriş yapar, JWT döndürür (UC2)",
)
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> TokenResponse:
    normalized_email = payload.email.strip().lower()
    client_ip = get_client_ip(request) or "unknown"
    rate_key = f"{client_ip}:{normalized_email}"

    if login_rate_limiter.is_limited(rate_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Çok fazla başarısız giriş denemesi. Lütfen birkaç dakika sonra tekrar deneyin.",
        )

    user = db.scalar(select(User).where(func.lower(User.email) == normalized_email))
    if user is None or not user.is_active or user.deleted_at is not None:
        login_rate_limiter.record_failure(rate_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-posta veya şifre hatalı.",
        )

    if not verify_password(payload.password, user.password_hash):
        login_rate_limiter.record_failure(rate_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-posta veya şifre hatalı.",
        )

    login_rate_limiter.reset(rate_key)
    token, expires_in = create_access_token(user.id, user.role.value)

    record_audit(
        db,
        actor_user_id=user.id,
        action="user.login",
        entity_type="user",
        entity_id=user.id,
        ip_address=get_client_ip(request),
    )
    db.commit()

    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserResponse.model_validate(user),
    )


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Giriş yapmış kullanıcının bilgilerini döndürür",
)
def get_me(current_user: User = Depends(get_current_user)) -> User:
    return current_user