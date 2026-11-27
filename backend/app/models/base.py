from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Enum as SAEnum, Identity, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base  # noqa: F401


def pk_column():
    return mapped_column(BigInteger, Identity(), primary_key=True)


def enum_type(enum_cls: type[StrEnum], name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=30,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
    )


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), sort_order=100)


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), sort_order=101
    )