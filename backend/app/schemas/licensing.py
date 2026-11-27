"""Lisans talebi, sözleşme, ödeme ve gelir paylaşımı Pydantic şemaları."""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import (
    ApprovalParty,
    IntendedUse,
    PaymentFailureReason,
    PaymentProvider,
    PaymentStatus,
    RecipientType,
    RequestDecision,
    RequestStatus,
)


class LicenseRequestCreate(BaseModel):
    work_id: int = Field(gt=0)
    intended_use: IntendedUse
    purpose_description: str = Field(min_length=10, max_length=2000)


class LicenseRequestDecisionInput(BaseModel):
    decision: Literal[RequestDecision.APPROVED, RequestDecision.REJECTED]
    comment: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def require_comment_on_reject(self) -> "LicenseRequestDecisionInput":
        if self.decision == RequestDecision.REJECTED and not (self.comment or "").strip():
            raise ValueError("Reddetme kararında gerekçe (comment) belirtilmesi zorunludur.")
        return self


class ContractAcceptRequest(BaseModel):
    accepted: bool
    contract_template_version: str = Field(default="v1.0", min_length=1, max_length=32)

    @model_validator(mode="after")
    def must_be_accepted(self) -> "ContractAcceptRequest":
        if not self.accepted:
            raise ValueError("Sözleşme metni kabul edilmeden sonraki adıma geçilemez.")
        return self


class PaymentExecuteRequest(BaseModel):
    provider: PaymentProvider = PaymentProvider.INTERNAL_SIMULATION
    idempotency_key: str = Field(min_length=8, max_length=64)
    simulation_scenario: Literal[
        "success",
        "insufficient_funds",
        "invalid_card",
        "suspected_fraud",
        "provider_error",
    ] = "success"


class RequestApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_id: int
    party: ApprovalParty
    user_id: int
    decision: RequestDecision
    comment: str | None = None
    decided_at: datetime


class LicenseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_id: int
    license_number: str
    contract_template_version: str
    is_sample_contract: bool
    starts_on: date
    ends_on: date
    created_at: datetime


class RevenueShareResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    payment_id: int
    recipient_type: RecipientType
    recipient_user_id: int | None = None
    recipient_org_id: int | None = None
    share_percent: Decimal
    amount: Decimal
    created_at: datetime


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_id: int
    amount: Decimal
    currency: str
    status: PaymentStatus
    provider: PaymentProvider
    provider_reference: str | None = None
    failure_reason: PaymentFailureReason | None = None
    idempotency_key: str
    completed_at: datetime | None = None
    created_at: datetime
    revenue_shares: list[RevenueShareResponse] = []


class PaymentAttemptResponse(BaseModel):
    payment: PaymentResponse
    request_status: RequestStatus
    license: LicenseResponse | None = None
    idempotent_replay: bool = False
    error_message: str | None = None


class LicenseRequestResponse(BaseModel):
    id: int
    work_id: int
    work_title: str | None = None
    work_isbn: str | None = None
    terms_id: int
    fee_amount: Decimal | None = None
    currency: str = "TRY"
    duration_months: int | None = None
    licensee_user_id: int
    licensee_org_id: int | None = None
    intended_use: IntendedUse
    purpose_description: str
    status: RequestStatus
    rejection_reason: str | None = None
    created_at: datetime
    updated_at: datetime
    approvals: list[RequestApprovalResponse] = []
    license: LicenseResponse | None = None
    payments: list[PaymentResponse] = []


class RevenueSummaryResponse(BaseModel):
    total_amount: Decimal
    count: int
    items: list[RevenueShareResponse]


class ExpireLicensesResponse(BaseModel):
    expired_count: int
    reminded_count: int