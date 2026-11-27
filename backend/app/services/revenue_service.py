"""Gelir paylaşımı ve kuruş yuvarlama hesaplama servisi (SCRUM-91: BFR-25, B7)."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from app.config import ayarlar

_KURUS = Decimal("0.01")
_PCT4 = Decimal("0.0001")
_HUNDRED = Decimal("100")


def _q2(value: Decimal) -> Decimal:
    return value.quantize(_KURUS, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class AuthorShareAllocation:
    author_id: int
    split_percent: Decimal
    effective_share_percent: Decimal
    amount: Decimal


@dataclass(frozen=True)
class RevenueBreakdown:
    total_amount: Decimal
    platform_share_percent: Decimal
    platform_amount: Decimal
    publisher_share_percent: Decimal
    publisher_amount: Decimal
    author_allocations: list[AuthorShareAllocation]


def calculate_revenue_shares(
    *,
    total_amount: Decimal,
    author_share_pct: Decimal,
    publisher_share_pct: Decimal,
    author_splits: list[tuple[int, Decimal]],
    commission_rate: Decimal | None = None,
) -> RevenueBreakdown:
    """Lisans bedelini %15 platform komisyonu ve sözleşme oranlarına göre kuruşuna kadar bölüştürür.

    Yuvarlama kuralı (karar_ve_kurallar.md):
    1. platform = round(toplam * komisyon_orani, 2)
    2. yazar_havuzu = round((toplam - platform) * (author_share_pct / 100), 2)
    3. yayinevi = toplam - platform - yazar_havuzu
    Çoklu yazar varsa yazar havuzu split_percent oranında bölünür, son kuruş farkı son yazara yazılır.
    """
    rate = commission_rate if commission_rate is not None else ayarlar().komisyon_orani
    total = _q2(Decimal(total_amount))
    author_pct = Decimal(author_share_pct)
    publisher_pct = Decimal(publisher_share_pct)

    if total <= Decimal("0"):
        raise ValueError("Ödeme tutarı sıfırdan büyük olmalıdır.")
    if author_pct < 0 or publisher_pct < 0 or author_pct + publisher_pct != _HUNDRED:
        raise ValueError("Yazar ve yayınevi oranlarının toplamı 100 olmalıdır.")
    if not author_splits:
        raise ValueError("En az bir yazar payı belirtilmelidir.")

    splits_sum = sum((Decimal(sp) for _, sp in author_splits), Decimal("0"))
    if splits_sum != _HUNDRED:
        raise ValueError("Yazar pay oranlarının (split_percent) toplamı 100 olmalıdır.")

    platform_amount = _q2(total * rate)
    distributable = total - platform_amount
    author_pool = _q2(distributable * author_pct / _HUNDRED)
    publisher_amount = total - platform_amount - author_pool

    net_ratio = Decimal("1") - rate
    platform_share_percent = (rate * _HUNDRED).quantize(_PCT4, rounding=ROUND_HALF_UP)
    publisher_share_percent = (net_ratio * publisher_pct).quantize(_PCT4, rounding=ROUND_HALF_UP)

    allocations: list[AuthorShareAllocation] = []
    allocated_author_total = Decimal("0.00")

    for idx, (author_id, raw_split) in enumerate(author_splits):
        split_pct = Decimal(raw_split)
        eff_pct = (net_ratio * author_pct * split_pct / _HUNDRED).quantize(
            _PCT4, rounding=ROUND_HALF_UP
        )
        if idx == len(author_splits) - 1:
            amount = author_pool - allocated_author_total
        else:
            amount = _q2(author_pool * split_pct / _HUNDRED)
            allocated_author_total += amount
        allocations.append(
            AuthorShareAllocation(
                author_id=author_id,
                split_percent=split_pct,
                effective_share_percent=eff_pct,
                amount=amount,
            )
        )

    return RevenueBreakdown(
        total_amount=total,
        platform_share_percent=platform_share_percent,
        platform_amount=platform_amount,
        publisher_share_percent=publisher_share_percent,
        publisher_amount=publisher_amount,
        author_allocations=allocations,
    )