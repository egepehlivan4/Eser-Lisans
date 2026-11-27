"""Kimlik, kayıt ve yayınevi onay Pydantic şemaları."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.models.enums import ApprovalStatus, OrganizationType, UserRole


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    tax_or_mersis_no: str = Field(min_length=10, max_length=20)
    corporate_email: EmailStr


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: OrganizationType
    name: str
    tax_or_mersis_no: str
    corporate_email: str
    approval_status: ApprovalStatus
    reviewed_at: datetime | None = None
    rejection_reason: str | None = None
    created_at: datetime


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    role: Literal[UserRole.PUBLISHER_STAFF, UserRole.AUTHOR, UserRole.LICENSEE]
    privacy_notice_accepted: bool
    pilot_consent_accepted: bool = False
    organization: OrganizationCreate | None = None
    invitation_token: str | None = None

    @model_validator(mode="after")
    def validate_role_requirements(self) -> "RegisterRequest":
        if not self.privacy_notice_accepted:
            raise ValueError("KVKK aydınlatma metni onayı zorunludur.")
        if self.role == UserRole.PUBLISHER_STAFF and self.organization is None:
            raise ValueError("Yayınevi Yetkilisi kaydı için kurum bilgileri zorunludur.")
        if self.role == UserRole.AUTHOR and self.organization is not None:
            raise ValueError("Yazar rolü için kurum bilgisi girilmemelidir.")
        return self


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    first_name: str
    last_name: str
    role: UserRole
    author_code: str | None = None
    organization_id: int | None = None
    organization: OrganizationResponse | None = None
    is_active: bool
    privacy_notice_accepted_at: datetime
    pilot_consent_at: datetime | None = None
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class OrganizationReviewRequest(BaseModel):
    decision: Literal[ApprovalStatus.APPROVED, ApprovalStatus.REJECTED]
    rejection_reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_rejection_reason(self) -> "OrganizationReviewRequest":
        if self.decision == ApprovalStatus.REJECTED and not (self.rejection_reason or "").strip():
            raise ValueError("Reddetme işleminde gerekçe belirtilmesi zorunludur.")
        return self