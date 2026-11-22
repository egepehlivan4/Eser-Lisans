"""Uygulama ayarları. Değerler ortam değişkenlerinden veya .env dosyasından okunur."""
from decimal import Decimal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Ayarlar(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://eserlisans:eserlisans@localhost:55432/eserlisans"
    jwt_gizli_anahtar: str = "gelistirme-ortami-icin-varsayilan-anahtar-degistirin"
    jwt_algoritma: str = "HS256"
    jwt_sure_dakika: int = 60
    # Platformun her lisans bedelinden aldığı komisyon (0.15 = %15)
    komisyon_orani: Decimal = Decimal("0.15")

    # E-posta: smtp_sunucu boşsa e-postalar gönderilmez, terminale yazılır (geliştirme ortamı).
    smtp_sunucu: str = ""
    smtp_port: int = 587
    smtp_kullanici: str = ""
    smtp_sifre: str = ""
    eposta_gonderen: str = "EserLisans <bildirim@eserlisans.local>"
    # Davet bağlantılarında kullanılan ön yüz adresi
    uygulama_adresi: str = "http://localhost:5173"

    # Dış ISBN servisleri (Google Books, Open Library). Testlerde ve çevrimdışı çalışırken kapatılabilir.
    dis_isbn_servisleri: bool = True


@lru_cache
def ayarlar() -> Ayarlar:
    return Ayarlar()
