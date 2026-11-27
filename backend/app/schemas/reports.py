"""Kullanım raporu, denetim günlüğü ve lisans raporu dışa aktarma şemaları."""
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import AiUsageType, IntendedUse, RequestStatus


class UsageReportCreate(BaseModel):
    usage_type: AiUsageType
    period_start: date
    period_end: date
    model_name: str | None = Field(default=None, max_length=200)
    description: str = Field(min_length=10, max_length=2000)
    declaration_accepted: bool

    @model_validator(mode="after")
    def validate_report(self) -> "UsageReportCreate":
        if not self.declaration_accepted:
            raise ValueError("Kullanım beyanı onayı (declaration_accepted) zorunludur.")
        if self.period_end < self.period_start:
            raise ValueError("Bitiş tarihi (period_end) başlangıç tarihinden önce olamaz.")
        return self


class UsageReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    license_id: int
    reported_by_user_id: int
    usage_type: AiUsageType
    period_start: date
    period_end: date
    model_name: str | None = None
    description: str
    declaration_accepted: bool
    reported_at: datetime


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    occurred_at: datetime
    actor_user_id: int | None = None
    action: str
    entity_type: str
    entity_id: str | None = None
    old_value: dict[str, Any] | None = None
    new_value: dict[str, Any] | None = None
    ip_address: str | None = None

    @field_validator("ip_address", mode="before")
    @classmethod
    def coerce_ip_to_str(cls, value: Any) -> str | None:
        return str(value) if value is not None else None


class LicenseReportRow(BaseModel):
    request_id: int
    license_number: str | None = None
    work_isbn: str
    work_title: str
    status: RequestStatus
    intended_use: IntendedUse
    fee_amount: Decimal
    currency: str
    starts_on: date | None = None
    ends_on: date | None = None
    my_revenue_share: Decimal
    platform_commission: Decimal
    usage_report_count: int
    created_at: datetime