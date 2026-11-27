"""Eser yönetimi, ISBN künye, yazar daveti ve lisans koşulu şemaları."""
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.models.enums import (
    ApprovalDecision,
    InvitationStatus,
    PermissionLevel,
    TermsStatus,
    WorkMetadataSource,
    WorkStatus,
)


class IsbnLookupResponse(BaseModel):
    isbn: str
    found: bool
    metadata_source: WorkMetadataSource | None = None
    title: str | None = None
    authors_text: str | None = None
    publisher_name: str | None = None
    publication_year: int | None = None
    language_code: str = "tr"
    category: str | None = None


class AuthorLookupResponse(BaseModel):
    id: int
    author_code: str
    first_name: str
    last_name: str
    email: str


class WorkAuthorInput(BaseModel):
    author_code: str | None = Field(default=None, max_length=20)
    email: EmailStr | None = None
    split_percent: Decimal = Field(default=Decimal("100.00"), gt=0, le=100)

    @model_validator(mode="after")
    def require_code_or_email(self) -> "WorkAuthorInput":
        if not self.author_code and not self.email:
            raise ValueError("Her yazar için author_code veya email belirtilmelidir.")
        return self


class TermsProposalInput(BaseModel):
    permission_level: PermissionLevel = PermissionLevel.UNAVAILABLE
    fee_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    currency: str = Field(default="TRY", min_length=3, max_length=3)
    duration_months: int = Field(default=36, gt=0, le=120)
    author_share_pct: Decimal = Field(default=Decimal("60.00"), ge=0, le=100)
    publisher_share_pct: Decimal = Field(default=Decimal("40.00"), ge=0, le=100)
    revision_note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_shares(self) -> "TermsProposalInput":
        if self.author_share_pct + self.publisher_share_pct != Decimal("100"):
            raise ValueError("Yazar ve yayınevi gelir paylaşım oranları toplamı 100 olmalıdır.")
        return self


class WorkCreateRequest(BaseModel):
    isbn: str = Field(min_length=10, max_length=20)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    publication_year: int | None = Field(default=None, ge=1500, le=2100)
    language_code: str | None = Field(default=None, max_length=10)
    category: str | None = Field(default=None, max_length=100)
    authors: list[WorkAuthorInput] = Field(min_length=1)
    terms: TermsProposalInput = Field(default_factory=TermsProposalInput)

    @model_validator(mode="after")
    def validate_author_splits(self) -> "WorkAuthorInput | WorkCreateRequest":
        total_split = sum((a.split_percent for a in self.authors), Decimal("0"))
        if total_split != Decimal("100"):
            raise ValueError("Yazarların pay oranları (split_percent) toplamı 100 olmalıdır.")
        return self


class TermsDecisionRequest(BaseModel):
    decision: Literal[ApprovalDecision.APPROVED, ApprovalDecision.REVISION_REQUESTED]
    comment: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_comment(self) -> "TermsDecisionRequest":
        if self.decision == ApprovalDecision.REVISION_REQUESTED and not (self.comment or "").strip():
            raise ValueError("Revizyon talebinde açıklama (comment) belirtilmelidir.")
        return self


class MarkUnavailableRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class AuthorInviteRequest(BaseModel):
    email: EmailStr
    split_percent: Decimal = Field(gt=0, le=100)


class WorkAuthorResponse(BaseModel):
    author_id: int
    author_code: str | None
    first_name: str
    last_name: str
    email: str
    split_percent: Decimal


class InvitationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    work_id: int
    split_percent: Decimal
    status: InvitationStatus
    expires_at: datetime
    created_at: datetime


class TermsApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    terms_id: int
    author_id: int
    decision: ApprovalDecision
    comment: str | None = None
    decided_at: datetime


class LicenseTermsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    work_id: int
    version_no: int
    permission_level: PermissionLevel
    fee_amount: Decimal
    currency: str
    duration_months: int
    author_share_pct: Decimal
    publisher_share_pct: Decimal
    status: TermsStatus
    proposed_by_user_id: int
    revision_note: str | None = None
    created_at: datetime
    approvals: list[TermsApprovalResponse] = []


class WorkResponse(BaseModel):
    id: int
    isbn: str
    title: str
    publisher_org_id: int
    publisher_name: str | None = None
    publication_year: int | None = None
    language_code: str
    category: str | None = None
    metadata_source: WorkMetadataSource
    status: WorkStatus
    registered_by_user_id: int
    created_at: datetime
    updated_at: datetime
    authors: list[WorkAuthorResponse] = []
    pending_invitations: list[InvitationResponse] = []
    current_terms: LicenseTermsResponse | None = None
    terms_versions: list[LicenseTermsResponse] = []