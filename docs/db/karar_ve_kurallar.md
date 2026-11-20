# EserLisans - Veritabanı Tasarım Kararları ve Ekip Sözleşmesi (Adım 1)

## 1. Genel Kurallar
- **İsimlendirme:** Tüm tablo, sütun ve API JSON alan adları İngilizce `snake_case` olacak (Türkçe karakter kullanılmayacak).
- **Kimlik (ID) Tipi:** Tüm tablolarda `bigint` kullanılacak (`isbn_catalog` hariç, orada PK = 13 haneli ISBN).
- **Para Alanları:** Kesinlikle `float` kullanılmayacak, tamamı `NUMERIC(12,2)` olacak.
- **Tarih/Saat:** Tamamı zaman dilimli `TIMESTAMPTZ` olacak.
- **Durum Alanları:** Esneklik için PostgreSQL native ENUM yerine `VARCHAR + CHECK` kısıtı kullanılacak.
- **Silme Kuralı:** TTK md. 82 ve KVKK uyumu gereği fiziksel silme (`CASCADE`) yapılmayacak, tüm ilişkilerde `ON DELETE RESTRICT` uygulanacak.
- **Gizlilik:** Kart numarası, IBAN ve düz metin şifre veritabanında kesinlikle tutulmayacak.

## 2. Gelir Paylaşımı ve Kuruş Yuvarlama Kuralı (B7 Ölçütü)
- Platform komisyonu: **%15** (uygulama sabiti, her ödemede `revenue_shares` tablosuna işlenir).
- Hesaplama sırası:
  1. `platform = round(toplam * 0.15, 2)`
  2. `yazar_havuzu = round((toplam - platform) * (author_share_pct / 100), 2)`
  3. `yayinevi = toplam - platform - yazar_havuzu` *(Kalan 1 kuruşluk yuvarlama farkı yayınevine yazılır, toplam her zaman ödeme tutarına birebir eşit çıkar.)*
- **Ücretsiz Lisans (`fee_amount = 0`):** Ödeme tablosuna (`payments`) kayıt atılmaz; hak sahipleri onayladığı anda lisans doğrudan `active` olur.

## 3. Lisans Talebi Durum Kodları (Backend <-> Frontend Eşleşmesi)
| Veritabanı / API Kodu | Ekranda Görünecek Türkçe İsim |
|---|---|
| `requested` | Talep Edildi |
| `awaiting_approval` | Hak Sahipleri Onayı Bekleniyor |
| `approved` | Onaylandı |
| `awaiting_payment` | Ödeme Bekleniyor |
| `active` | Aktif |
| `expired` | Süresi Doldu |
| `rejected` | Reddedildi |

## 4. Hangi Kural Nerede Kontrol Edilecek?
- **Veritabanında (Paket 1):** Oran toplamı = 100, bedel >= 0, aynı talebe tek başarılı ödeme, aynı eserde tek onaylı koşul sürümü, `audit_log` silme/güncelleme yasağı.
- **Backend Kodunda (Paket 2):** ISBN-13 kontrol hanesi doğrulaması, yazar payları toplamı = 100 kontrolü, durum geçiş sırası, ödeme + gelir payı + lisans oluşturmanın tek ACID transaction içinde yapılması.