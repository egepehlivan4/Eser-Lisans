# EserLisans

ISBN tabanlı yapay zekâ eğitim lisanslama ve telif paylaşım platformu. Gazi Üniversitesi BMT-315 Yazılım
Mühendisliği dönem projesi (Şube 1).

| Paket | Üye | Rol |
|---|---|---|
| 1 | Sude Aksu | Veritabanı ve Modelleme |
| 2 | Mervenur Ata | Arka Uç |
| 3 | Gülsüm Durmaz | Ön Yüz |
| 4 | Ege Pehlivan | Test ve Proje Yönetimi |

## Yapı

```
backend/     FastAPI + SQLAlchemy + Alembic (Python 3.13)
  app/       uygulama kodu (main.py, config.py, database.py)
  alembic/   veritabanı şema geçişleri
  tests/     pytest testleri
frontend/    React + TypeScript + Vite (Node.js 22)
.github/     her pull request'te çalışan otomatik testler
```

## Çalıştırma

Python 3.13, Node.js 22 ve Docker gerekir.

```bash
# 1) Veritabanı (PostgreSQL 18, localhost:55432)
docker compose up -d

# 2) Arka uç: http://localhost:8000/docs
cd backend
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload

# 3) Ön yüz: http://localhost:5173  (ayrı bir terminalde)
cd frontend
npm install
npm run dev
```

Ön yüz açıldığında "Arka uç bağlantısı: çalışıyor" yazıyorsa iskelet hazırdır.

Testler: `cd backend && python -m pytest -q` ve `cd frontend && npm run build`

## Çalışma düzeni

- Her iş Jira'da bir görevdir (`ESL-12` gibi). Görevi **Yapılıyor**'a çekince görev anahtarıyla dal açın:
  `git switch -c ESL-12-eser-kaydet`
- Commit mesajı görev anahtarıyla başlar: `ESL-12 ISBN ile eser kaydı ucu`
- Pull request açın ve görevi **İncelemede**'ye çekin. Bir arkadaşınız inceleyip birleştirince görev **Bitti** olur.
- `main` dalına doğrudan gönderim yapılmaz; her PR'da testler otomatik çalışır.
