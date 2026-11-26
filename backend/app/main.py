"""EserLisans API giriş noktası.

Çalıştırma:  uvicorn app.main:app --reload
Belgeler:    http://localhost:8000/docs  (Swagger UI)
"""
import logging

from fastapi import FastAPI


app = FastAPI(
    title="EserLisans API",
    description="ISBN tabanlı yapay zekâ eğitim lisanslama ve telif paylaşım platformu",
    version="0.2.0",
)


logging.basicConfig(level=logging.INFO)  # e-posta bildirimleri geliştirme ortamında terminale yazılır


@app.get("/api/saglik", tags=["Sistem"], summary="Servis ayakta mı?")
def saglik():
    return {"durum": "ok"}
