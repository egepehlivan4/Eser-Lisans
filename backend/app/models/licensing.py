from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint, func, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, TimestampMixin, enum_type, pk_column
from app.models.enums import (
    AiUsageType,
    ApprovalParty,
    IntendedUse,
    RequestDecision,
    RequestStatus,
)

if TYPE_CHECKING:
    from app.models.catalog import LicenseTerms, Work
    from app.models.identity import Organization, User
    from app.models.payments import Payment


class LicenseRequest(TimestampMixin, Base):
    __tablename__ = "license_requests"

    id: Mapped[int] = pk_column()
    work_id: Mapped[int] = mapped_column(
        ForeignKey("works.id", ondelete="RESTRICT"), index=True
    )
    terms_id: Mapped[int] = mapped_column(
        ForeignKey("license_terms.id", ondelete="RESTRICT"), index=True
    )
    licensee_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    licensee_org_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    intended_use: Mapped[IntendedUse] = mapped_column(enum_type(IntendedUse, "intended_use"))
    purpose_description: Mapped[str]
    status: Mapped[RequestStatus] = mapped_column(
        enum_type(RequestStatus, "status"),
        server_default=RequestStatus.REQUESTED.value,
        index=True,
    )
    rejection_reason: Mapped[str | None]

    work: Mapped[Work] = relationship(back_populates="requests")
    terms: Mapped[LicenseTerms] = relationship()
    licensee_user: Mapped[User] = relationship()
    licensee_org: Mapped[Organization | None] = relationship()
    approvals: Mapped[list[RequestApproval]] = relationship(back_populates="request")
    license: Mapped[License | None] = relationship(back_populates="request", uselist=False)
    payments: Mapped[list[Payment]] = relationship(back_populates="request")

    def __repr__(self) -> str:
        return f"<LicenseRequest id={self.id} work={self.work_id} status={self.status}>"


class RequestApproval(Base):
    __tablename__ = "request_approvals"

    id: Mapped[int] = pk_column()
    request_id: Mapped[int] = mapped_column(
        ForeignKey("license_requests.id", ondelete="RESTRICT"), index=True
    )
    party: Mapped[ApprovalParty] = mapped_column(enum_type(ApprovalParty, "party"))
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    decision: Mapped[RequestDecision] = mapped_column(enum_type(RequestDecision, "decision"))
    comment: Mapped[str | None]
    decided_at: Mapped[datetime] = mapped_column(server_default=func.now())

    request: Mapped[LicenseRequest] = relationship(back_populates="approvals")
    user: Mapped[User] = relationship()

    __table_args__ = (
        UniqueConstraint("request_id", "user_id", name="uq_request_approvals_request_id_user_id"),
    )


class License(CreatedAtMixin, Base):
    __tablename__ = "licenses"

    id: Mapped[int] = pk_column()
    request_id: Mapped[int] = mapped_column(
        ForeignKey("license_requests.id", ondelete="RESTRICT"), unique=True
    )
    license_number: Mapped[str] = mapped_column(String(30), unique=True)
    contract_template_version: Mapped[str] = mapped_column(String(32), server_default="v1.0")
    is_sample_contract: Mapped[bool] = mapped_column(server_default=true())
    starts_on: Mapped[date]
    ends_on: Mapped[date]

    request: Mapped[LicenseRequest] = relationship(back_populates="license")
    usage_reports: Mapped[list[UsageReport]] = relationship(back_populates="license")

    __table_args__ = (
        CheckConstraint("ends_on > starts_on", name="end_after_start"),
    )


class UsageReport(Base):
    __tablename__ = "usage_reports"

    id: Mapped[int] = pk_column()
    license_id: Mapped[int] = mapped_column(
        ForeignKey("licenses.id", ondelete="RESTRICT"), index=True
    )
    reported_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    usage_type: Mapped[AiUsageType] = mapped_column(enum_type(AiUsageType, "usage_type"))
    period_start: Mapped[date]
    period_end: Mapped[date]
    model_name: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str]
    declaration_accepted: Mapped[bool]
    reported_at: Mapped[datetime] = mapped_column(server_default=func.now())

    license: Mapped[License] = relationship(back_populates="usage_reports")
    reported_by: Mapped[User] = relationship()

    __table_args__ = (
        CheckConstraint("period_end >= period_start", name="period_order"),
        CheckConstraint("declaration_accepted = true", name="declaration_required"),
    )