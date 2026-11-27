"""Ödeme sağlayıcı adaptör katmanı (SCRUM-90: BFR-21, BFR-22)."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
import secrets

from app.models.enums import PaymentFailureReason, PaymentProvider


@dataclass(frozen=True)
class PaymentChargeResult:
    succeeded: bool
    provider_reference: str | None = None
    failure_reason: PaymentFailureReason | None = None
    error_message: str | None = None


_FAILURE_MESSAGES: dict[PaymentFailureReason, str] = {
    PaymentFailureReason.INSUFFICIENT_FUNDS: "Ödeme reddedildi: yetersiz bakiye.",
    PaymentFailureReason.INVALID_CARD: "Ödeme reddedildi: hatalı kart bilgisi.",
    PaymentFailureReason.SUSPECTED_FRAUD: "Ödeme reddedildi: şüpheli işlem uyarısı.",
    PaymentFailureReason.PROVIDER_ERROR: "Ödeme sağlayıcısına ulaşılamadı, lütfen tekrar deneyin.",
}


class PaymentAdapter(ABC):
    """Gerçek ortama geçişte yalnızca bu arayüzü uygulayan adaptör değiştirilir (BFR-21)."""

    @abstractmethod
    def charge(
        self,
        *,
        request_id: int,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
        simulation_scenario: str = "success",
    ) -> PaymentChargeResult:
        raise NotImplementedError


class InternalSimulationAdapter(PaymentAdapter):
    """Dahili güvenli ödeme simülasyonu (kart bilgisi saklanmaz)."""

    def charge(
        self,
        *,
        request_id: int,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
        simulation_scenario: str = "success",
    ) -> PaymentChargeResult:
        if simulation_scenario == "success":
            return PaymentChargeResult(
                succeeded=True,
                provider_reference=f"SIM-{request_id}-{secrets.token_hex(6).upper()}",
            )

        try:
            reason = PaymentFailureReason(simulation_scenario)
        except ValueError:
            reason = PaymentFailureReason.PROVIDER_ERROR

        return PaymentChargeResult(
            succeeded=False,
            provider_reference=f"SIM-FAIL-{secrets.token_hex(4).upper()}",
            failure_reason=reason,
            error_message=_FAILURE_MESSAGES[reason],
        )


class IyzicoSandboxAdapter(PaymentAdapter):
    """iyzico test (sandbox) adaptörü; prototip ve çevrimdışı testlerde güvenli sandbox yanıtı üretir."""

    def charge(
        self,
        *,
        request_id: int,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
        simulation_scenario: str = "success",
    ) -> PaymentChargeResult:
        if simulation_scenario == "success":
            return PaymentChargeResult(
                succeeded=True,
                provider_reference=f"IYZ-SBX-{request_id}-{secrets.token_hex(6).upper()}",
            )

        try:
            reason = PaymentFailureReason(simulation_scenario)
        except ValueError:
            reason = PaymentFailureReason.PROVIDER_ERROR

        return PaymentChargeResult(
            succeeded=False,
            provider_reference=f"IYZ-SBX-FAIL-{secrets.token_hex(4).upper()}",
            failure_reason=reason,
            error_message=_FAILURE_MESSAGES[reason],
        )


def get_payment_adapter(provider: PaymentProvider) -> PaymentAdapter:
    if provider == PaymentProvider.IYZICO_SANDBOX:
        return IyzicoSandboxAdapter()
    return InternalSimulationAdapter()