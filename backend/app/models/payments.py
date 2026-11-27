from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CHAR, CheckConstraint, ForeignKey, Index, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, enum_type, pk_column
from app.models.enums import (
    PaymentFailureReason,
    PaymentProvider,
    PaymentStatus,
    RecipientType,
)

if TYPE_CHECKING:
    from app.models.identity import Organization, User
    from app.models.licensing import LicenseRequest


class Payment(CreatedAtMixin, Base):
    __tablename__ = "payments"

    id: Mapped[int] = pk_column()
    request_id: Mapped[int] = mapped_column(
        ForeignKey("license_requests.id", ondelete="RESTRICT"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(CHAR(3), server_default="TRY")
    status: Mapped[PaymentStatus] = mapped_column(
        enum_type(PaymentStatus, "status"), server_default=PaymentStatus.PENDING.value
    )
    provider: Mapped[PaymentProvider] = mapped_column(enum_type(PaymentProvider, "provider"))
    provider_reference: Mapped[str | None] = mapped_column(String(100))
    failure_reason: Mapped[PaymentFailureReason | None] = mapped_column(
        enum_type(PaymentFailureReason, "failure_reason")
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    completed_at: Mapped[datetime | None]

    request: Mapped[LicenseRequest] = relationship(back_populates="payments")
    revenue_shares: Mapped[list[RevenueShare]] = relationship(back_populates="payment")

    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "(status = 'failed') = (failure_reason IS NOT NULL)",
            name="failure_reason_iff_failed",
        ),
        Index(
            "uq_payments_one_succeeded_per_request",
            "request_id",
            unique=True,
            postgresql_where=text("status = 'succeeded'"),
        ),
    )

    def __repr__(self) -> str:
        return f"<Payment id={self.id} request={self.request_id} {self.amount} {self.status}>"


class RevenueShare(CreatedAtMixin, Base):
    __tablename__ = "revenue_shares"

    id: Mapped[int] = pk_column()
    payment_id: Mapped[int] = mapped_column(
        ForeignKey("payments.id", ondelete="RESTRICT"), index=True
    )
    recipient_type: Mapped[RecipientType] = mapped_column(
        enum_type(RecipientType, "recipient_type")
    )
    recipient_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    recipient_org_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    share_percent: Mapped[Decimal] = mapped_column(Numeric(7, 4))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))

    payment: Mapped[Payment] = relationship(back_populates="revenue_shares")
    recipient_user: Mapped[User | None] = relationship()
    recipient_org: Mapped[Organization | None] = relationship()

    __table_args__ = (
        CheckConstraint("share_percent >= 0 AND share_percent <= 100", name="share_percent_range"),
        CheckConstraint("amount >= 0", name="amount_nonnegative"),
        CheckConstraint(
            "(recipient_type = 'author' AND recipient_user_id IS NOT NULL AND recipient_org_id IS NULL) "
            "OR (recipient_type = 'publisher' AND recipient_org_id IS NOT NULL AND recipient_user_id IS NULL) "
            "OR (recipient_type = 'platform' AND recipient_user_id IS NULL AND recipient_org_id IS NULL)",
            name="recipient_matches_type",
        ),
        Index(
            "uq_revenue_shares_payment_recipient",
            "payment_id",
            "recipient_type",
            "recipient_user_id",
            "recipient_org_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )