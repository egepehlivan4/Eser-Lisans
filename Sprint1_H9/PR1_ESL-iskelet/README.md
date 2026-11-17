# EserLisans

ISBN tabanlı yapay zekâ eğitim lisanslama ve telif paylaşım platformu. Gazi Üniversitesi BMT-315 Yazılım
Mühendisliği dönem projesi (Şube 1).

| Paket | Üye | Rol |
|---|---|---|
| 1 | Sude Aksu | Veritabanı ve Modelleme Sorumlusu |
| 2 | Mervenur Ata | Arka Uç Geliştirici |
| 3 | Gülsüm Durmaz | Ön Yüz Geliştirici |
| 4 | Ege Pehlivan | Proje Yöneticisi, Test ve Dağıtım Sorumlusu |

## Yapı

- `backend/`: arka uç (FastAPI, SQLAlchemy, PostgreSQL)
- `frontend/`: ön yüz (React, TypeScript, Vite)

## Yerel veritabanı

```bash
docker compose up -d     # PostgreSQL 18, localhost:55432
```

Arka uç ve ön yüz kurulum adımları ilgili parçalar eklendikçe bu dosyaya yazılacak.

## Çalışma düzeni

- Her iş Jira'da bir görevdir (`ESL-12` gibi). Görevi "Yapılıyor"a çekince görev anahtarıyla dal açın:
  `git switch -c ESL-12-eser-kaydet`
- Commit mesajı görev anahtarıyla başlar: `ESL-12 ISBN ile eser kaydı ucu`
- Pull request açın, görevi "İncelemede"ye çekin. Bir arkadaşınız inceleyip birleştirince görev "Bitti"ye geçer.
- `main` dalına doğrudan gönderim yapılmaz.
