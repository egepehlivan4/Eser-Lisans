"""İşlem bildirimi e-posta servisi (pazarlama e-postası gönderilmez — BFR-31)."""
from email.message import EmailMessage
import logging
import smtplib

from app.config import ayarlar

logger = logging.getLogger("eserlisans.email")


def send_transactional_email(to_email: str, subject: str, body: str) -> None:
    cfg = ayarlar()
    if not cfg.smtp_sunucu:
        logger.info("[E-POSTA SIMULASYON] Kime=%s | Konu=%s | Icerik=%s", to_email, subject, body)
        return

    msg = EmailMessage()
    msg["From"] = cfg.eposta_gonderen
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(cfg.smtp_sunucu, cfg.smtp_port, timeout=10) as server:
            server.starttls()
            if cfg.smtp_kullanici:
                server.login(cfg.smtp_kullanici, cfg.smtp_sifre)
            server.send_message(msg)
    except Exception:
        logger.exception("E-posta gonderimi basarisiz: to=%s subject=%s", to_email, subject)