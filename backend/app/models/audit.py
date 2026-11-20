from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, event, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from app.models.base import Base, pk_column

if TYPE_CHECKING:
    from app.models.identity import User


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = pk_column()
    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now())
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    action: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    old_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    new_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(INET)

    actor: Mapped[User | None] = relationship()

    __table_args__ = (
        Index("ix_audit_log_entity", "entity_type", "entity_id"),
        Index("ix_audit_log_occurred_at", "occurred_at"),
    )


@event.listens_for(AuditLog, "before_update")
def _forbid_audit_update(mapper, connection, target) -> None:
    raise PermissionError("audit_log records are immutable: UPDATE is forbidden.")


@event.listens_for(AuditLog, "before_delete")
def _forbid_audit_delete(mapper, connection, target) -> None:
    raise PermissionError("audit_log records are immutable: DELETE is forbidden.")


@event.listens_for(Session, "do_orm_execute")
def _forbid_audit_bulk_mutation(execute_state) -> None:
    if execute_state.is_update or execute_state.is_delete:
        target_table = getattr(execute_state.statement, "table", None)
        if target_table is not None and target_table.name == AuditLog.__tablename__:
            raise PermissionError(
                "audit_log table is immutable: bulk UPDATE/DELETE is forbidden."
            )