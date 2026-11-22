"""Proje iskeletinin ilk testi: API ayağa kalkıyor mu?"""
from fastapi.testclient import TestClient

from app.main import app


def test_saglik():
    yanit = TestClient(app).get("/api/saglik")
    assert yanit.status_code == 200
    assert yanit.json() == {"durum": "ok"}
