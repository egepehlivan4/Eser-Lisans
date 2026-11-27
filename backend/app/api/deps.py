"""FastAPI bağımlılıkları: oturum, kimlik doğrulama ve rol tabanlı yetkilendirme (RBAC)."""
from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.database import get_db
from app.models.enums import ApprovalStatus, UserRole
from app.models.identity import User

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Kimlik doğrulama belirteci (Bearer token) gereklidir.",
        )

    try:
        payload = decode_access_token(credentials.credentials)
        user_id = int(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Geçersiz veya süresi dolmuş erişim belirteci.",
        )

    user = db.get(User, user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Kullanıcı hesabı aktif değil veya bulunamadı.",
        )
    return user


def require_roles(*allowed_roles: UserRole) -> Callable[..., User]:
    def _checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bu işlem için yetkiniz bulunmamaktadır.",
            )
        return current_user

    return _checker


def require_approved_publisher(
    current_user: User = Depends(require_roles(UserRole.PUBLISHER_STAFF)),
) -> User:
    """Onaylanmamış yayınevlerinin eser kaydetmesini engeller (BFR-05)."""
    org = current_user.organization
    if org is None or org.approval_status != ApprovalStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Yayınevi hesabınız henüz Sistem Yöneticisi tarafından onaylanmadı.",
        )
    return current_user