from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, TimestampMixin, enum_type, pk_column
from app.models.enums import (
    ApprovalDecision,
    CatalogSource,
    PermissionLevel,
    TermsStatus,
    WorkMetadataSource,
    WorkStatus,
)

if TYPE_CHECKING:
    from app.models.identity import Organization, User
    from app.models.licensing import LicenseRequest


class IsbnCatalog(Base):
    __tablename__ = "isbn_catalog"

    isbn: Mapped[str] = mapped_column(CHAR(13), primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    authors_text: Mapped[str | None] = mapped_column(String(500))
    publisher_name: Mapped[str | None] = mapped_column(String(200))
    publication_year: Mapped[int | None] = mapped_column(SmallInteger)
    language_code: Mapped[str] = mapped_column(String(10), server_default="tr")
    category: Mapped[str | None] = mapped_column(String(100))
    source: Mapped[CatalogSource] = mapped_column(enum_type(CatalogSource, "source"))
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        CheckConstraint("isbn ~ '^[0-9]{13}$'", name="isbn_format"),
    )


class Work(TimestampMixin, Base):
    __tablename__ = "works"

    id: Mapped[int] = pk_column()
    isbn: Mapped[str] = mapped_column(CHAR(13), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    publisher_org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    publication_year: Mapped[int | None] = mapped_column(SmallInteger)
    language_code: Mapped[str] = mapped_column(String(10), server_default="tr")
    category: Mapped[str | None] = mapped_column(String(100))
    metadata_source: Mapped[WorkMetadataSource] = mapped_column(
        enum_type(WorkMetadataSource, "metadata_source")
    )
    status: Mapped[WorkStatus] = mapped_column(
        enum_type(WorkStatus, "status"),
        server_default=WorkStatus.PENDING_APPROVAL.value,
        index=True,
    )
    registered_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )

    publisher: Mapped[Organization] = relationship(back_populates="works")
    registered_by: Mapped[User] = relationship(foreign_keys=[registered_by_user_id])
    author_links: Mapped[list[WorkAuthor]] = relationship(back_populates="work")
    terms_versions: Mapped[list[LicenseTerms]] = relationship(
        back_populates="work", order_by="LicenseTerms.version_no"
    )
    requests: Mapped[list[LicenseRequest]] = relationship(back_populates="work")

    __table_args__ = (
        CheckConstraint("isbn ~ '^[0-9]{13}$'", name="isbn_format"),
    )

    def __repr__(self) -> str:
        return f"<Work id={self.id} isbn={self.isbn} status={self.status}>"


class WorkAuthor(CreatedAtMixin, Base):
    __tablename__ = "work_authors"

    work_id: Mapped[int] = mapped_column(
        ForeignKey("works.id", ondelete="RESTRICT"), primary_key=True
    )
    author_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True, index=True
    )
    split_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default="100")

    work: Mapped[Work] = relationship(back_populates="author_links")
    author: Mapped[User] = relationship()

    __table_args__ = (
        CheckConstraint("split_percent > 0 AND split_percent <= 100", name="split_percent_range"),
    )


class LicenseTerms(CreatedAtMixin, Base):
    __tablename__ = "license_terms"

    id: Mapped[int] = pk_column()
    work_id: Mapped[int] = mapped_column(
        ForeignKey("works.id", ondelete="RESTRICT"), index=True
    )
    version_no: Mapped[int]
    permission_level: Mapped[PermissionLevel] = mapped_column(
        enum_type(PermissionLevel, "permission_level")
    )
    fee_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), server_default="0")
    currency: Mapped[str] = mapped_column(CHAR(3), server_default="TRY")
    duration_months: Mapped[int] = mapped_column(SmallInteger, server_default="36")
    author_share_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    publisher_share_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    status: Mapped[TermsStatus] = mapped_column(
        enum_type(TermsStatus, "status"), server_default=TermsStatus.PROPOSED.value
    )
    proposed_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    revision_note: Mapped[str | None]

    work: Mapped[Work] = relationship(back_populates="terms_versions")
    proposed_by: Mapped[User] = relationship()
    approvals: Mapped[list[TermsApproval]] = relationship(back_populates="terms")

    __table_args__ = (
        UniqueConstraint("work_id", "version_no", name="uq_license_terms_work_id_version_no"),
        CheckConstraint("version_no > 0", name="version_no_positive"),
        CheckConstraint("fee_amount >= 0", name="fee_nonnegative"),
        CheckConstraint("duration_months > 0", name="duration_positive"),
        CheckConstraint(
            "author_share_pct >= 0 AND publisher_share_pct >= 0 "
            "AND author_share_pct + publisher_share_pct = 100",
            name="shares_sum_100",
        ),
        Index(
            "uq_license_terms_one_approved_per_work",
            "work_id",
            unique=True,
            postgresql_where=text("status = 'approved'"),
        ),
    )

    def __repr__(self) -> str:
        return f"<LicenseTerms id={self.id} work={self.work_id} v{self.version_no} {self.status}>"


class TermsApproval(Base):
    __tablename__ = "terms_approvals"

    id: Mapped[int] = pk_column()
    terms_id: Mapped[int] = mapped_column(
        ForeignKey("license_terms.id", ondelete="RESTRICT"), index=True
    )
    author_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    decision: Mapped[ApprovalDecision] = mapped_column(enum_type(ApprovalDecision, "decision"))
    comment: Mapped[str | None]
    decided_at: Mapped[datetime] = mapped_column(server_default=func.now())

    terms: Mapped[LicenseTerms] = relationship(back_populates="approvals")
    author: Mapped[User] = relationship()

    __table_args__ = (
        UniqueConstraint("terms_id", "author_id", name="uq_terms_approvals_terms_id_author_id"),
    )