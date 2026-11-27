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
from app.routers import admin, auth

logging.basicConfig(level=logging.INFO)  # e-posta bildirimleri geliştirme ortamında terminale yazılır

app = FastAPI(
    title="EserLisans API",
    description="ISBN tabanlı yapay zekâ eğitim lisanslama ve telif paylaşım platformu",
    version="0.3.0",
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


@app.get("/api/saglik", tags=["Sistem"], summary="Servis ayakta mı?")
def saglik():
    return {"durum": "ok"}


@app.get("/api/saglik/db", tags=["Sistem"], summary="Veritabanı bağlantısı aktif mi?")
def saglik_db(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"durum": "ok", "veritabani": "bagli"}