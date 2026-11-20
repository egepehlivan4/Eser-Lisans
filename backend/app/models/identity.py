from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CHAR, CheckConstraint, ForeignKey, Index, Numeric, String, func, text, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, TimestampMixin, enum_type, pk_column
from app.models.enums import (
    ApprovalStatus,
    InvitationStatus,
    OrganizationType,
    UserRole,
)

if TYPE_CHECKING:
    from app.models.catalog import Work


class Organization(TimestampMixin, Base):
    __tablename__ = "organizations"

    id: Mapped[int] = pk_column()
    type: Mapped[OrganizationType] = mapped_column(enum_type(OrganizationType, "type"))
    name: Mapped[str] = mapped_column(String(200))
    tax_or_mersis_no: Mapped[str] = mapped_column(String(20), unique=True)
    corporate_email: Mapped[str] = mapped_column(String(254))
    approval_status: Mapped[ApprovalStatus] = mapped_column(
        enum_type(ApprovalStatus, "approval_status"),
        server_default=ApprovalStatus.PENDING.value,
    )
    reviewed_at: Mapped[datetime | None]
    rejection_reason: Mapped[str | None]

    users: Mapped[list[User]] = relationship(back_populates="organization")
    works: Mapped[list[Work]] = relationship(back_populates="publisher")

    def __repr__(self) -> str:
        return f"<Organization id={self.id} name={self.name!r} type={self.type}>"


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = pk_column()
    email: Mapped[str] = mapped_column(String(254))
    password_hash: Mapped[str] = mapped_column(String(255))
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    role: Mapped[UserRole] = mapped_column(enum_type(UserRole, "role"))
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    author_code: Mapped[str | None] = mapped_column(String(20), unique=True)
    is_active: Mapped[bool] = mapped_column(server_default=true())
    privacy_notice_accepted_at: Mapped[datetime]
    pilot_consent_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]
    anonymized_at: Mapped[datetime | None]

    organization: Mapped[Organization | None] = relationship(back_populates="users")

    __table_args__ = (
        CheckConstraint(
            "(role = 'author') = (author_code IS NOT NULL)",
            name="author_code_only_for_authors",
        ),
        CheckConstraint(
            "role <> 'publisher_staff' OR organization_id IS NOT NULL",
            name="publisher_staff_has_org",
        ),
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role}>"


Index("uq_users_email_lower", func.lower(User.email), unique=True)


class Invitation(CreatedAtMixin, Base):
    __tablename__ = "invitations"

    id: Mapped[int] = pk_column()
    email: Mapped[str] = mapped_column(String(254))
    invited_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    work_id: Mapped[int] = mapped_column(
        ForeignKey("works.id", ondelete="RESTRICT"), index=True
    )
    split_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default="100")
    token_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)
    status: Mapped[InvitationStatus] = mapped_column(
        enum_type(InvitationStatus, "status"),
        server_default=InvitationStatus.PENDING.value,
    )
    expires_at: Mapped[datetime]
    accepted_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )

    invited_by: Mapped[User] = relationship(foreign_keys=[invited_by_user_id])
    accepted_user: Mapped[User | None] = relationship(foreign_keys=[accepted_user_id])
    work: Mapped[Work] = relationship()

    __table_args__ = (
        CheckConstraint("split_percent > 0 AND split_percent <= 100", name="split_percent_range"),
        CheckConstraint("char_length(token_hash) = 64", name="token_hash_len"),
    )


Index(
    "uq_invitations_pending_work_email",
    Invitation.work_id,
    func.lower(Invitation.email),
    unique=True,
    postgresql_where=text("status = 'pending'"),
)