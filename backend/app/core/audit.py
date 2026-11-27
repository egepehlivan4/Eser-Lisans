"""Değiştirilemez denetim günlüğü (audit_log) kayıt yardımcısı."""
import ipaddress
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.audit import AuditLog


def get_client_ip(request: Request | None) -> str | None:
    """İstemcinin geçerli IPv4/IPv6 adresini çıkarır; geçersizse None döner."""
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    candidate = forwarded.split(",")[0].strip() if forwarded else None
    if not candidate and request.client:
        candidate = request.client.host
    if not candidate:
        return None
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def record_audit(
    db: Session,
    *,
    actor_user_id: int | None,
    action: str,
    entity_type: str,
    entity_id: str | int | None = None,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        old_value=old_value,
        new_value=new_value,
        ip_address=ip_address,
    )
    db.add(entry)
    return entry