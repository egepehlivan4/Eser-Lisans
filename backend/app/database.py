"""Veritabanı bağlantısı ve oturum yönetimi."""
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import ayarlar


class Base(DeclarativeBase):
    pass


engine = create_engine(ayarlar().database_url, pool_pre_ping=True)
OturumFabrikasi = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Her istek için bir oturum açar; istek bitince kapatır."""
    db = OturumFabrikasi()
    try:
        yield db
    finally:
        db.close()
