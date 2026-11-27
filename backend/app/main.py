"""EserLisans API giriş noktası.

Çalıştırma:  uvicorn app.main:app --reload
Belgeler:    http://localhost:8000/docs  (Swagger UI)
"""
import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import ayarlar
from app.database import get_db
from app.routers import admin, auth, licensing, reports, works

logging.basicConfig(level=logging.INFO)  # e-posta bildirimleri geliştirme ortamında terminale yazılır

OPENAPI_TAGS = [
    {
        "name": "Sistem",
        "description": "Servis ve veritabanı sağlık kontrolleri.",
    },
    {
        "name": "Kimlik ve Yetkilendirme",
        "description": "Kayıt (KVKK ve pilot onamı), Argon2 + JWT giriş, hız sınırı ve profil (UC1–UC2).",
    },
    {
        "name": "Sistem Yönetimi",
        "description": "Yayınevi hesaplarının belge yüklemeden kurum metin bilgileriyle onay/ret süreci (UC3).",
    },
    {
        "name": "Eser Yönetimi",
        "description": (
            "Hibrit ISBN çözümleme, yazar ID seçimi/daveti, teklif-onay (maker-checker) akışı ve "
            "Türkçe karakter normalizasyonlu eser arama (UC4–UC9)."
        ),
    },
    {
        "name": "Lisanslama ve Ödeme",
        "description": (
            "Lisans talebi, satır kilitlemeli çift onay, durum makinesi, örnek sözleşme kabulü, "
            "ödeme adaptörü (simülasyon / iyzico test), %15 komisyonlu gelir paylaşımı ve süre dolumu (UC10–UC14)."
        ),
    },
    {
        "name": "Raporlama ve Denetim",
        "description": (
            "Aktif lisans kullanım beyanları, değiştirilemez denetim günlüğü (audit_log) ve "
            "CSV/JSON lisans raporu dışa aktarımı (UC15–UC17)."
        ),
    },
]

API_DESCRIPTION = """
**EserLisans**, yazar ile yayınevinin birlikte onay verdiği Türkçe eserlerin yapay zekâ eğitim lisanslamasını
ve telif paylaşımını uçtan uca izlenebilir kılan REST API servisidir.

### Yetkilendirme
Korumalı uç noktalar `Authorization: Bearer <JWT>` başlığı ile çalışır.

### Lisans Durum Makinesi
`requested` → `awaiting_approval` → `approved` → `awaiting_payment` → `active` → `expired`
*(Ücretsiz lisanslarda `approved` → `active`; taraflardan biri reddederse `rejected`.)*

### Gelir Paylaşımı (B7)
Lisans bedelinden **%15 platform komisyonu** düşülür, kalan **%85** sözleşme oranına göre yazar(lar) ve yayınevi
arasında kuruş düzeyinde bölüştürülür (kuruş yuvarlama farkı yayınevine yazılır).
"""

app = FastAPI(
    title="EserLisans API",
    description=API_DESCRIPTION.strip(),
    version="1.0.0",
    openapi_tags=OPENAPI_TAGS,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[ayarlar().uygulama_adresi, "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(works.router)
app.include_router(licensing.router)
app.include_router(reports.router)


@app.get("/api/saglik", tags=["Sistem"], summary="Servis ayakta mı?")
def saglik():
    return {"durum": "ok"}


@app.get("/api/saglik/db", tags=["Sistem"], summary="Veritabanı bağlantısı aktif mi?")
def saglik_db(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"durum": "ok", "veritabani": "bagli"}