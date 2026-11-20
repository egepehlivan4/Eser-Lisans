from enum import StrEnum


class UserRole(StrEnum):
    PUBLISHER_STAFF = "publisher_staff"
    AUTHOR = "author"
    LICENSEE = "licensee"
    ADMIN = "admin"


class OrganizationType(StrEnum):
    PUBLISHER = "publisher"
    LICENSEE = "licensee"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class CatalogSource(StrEnum):
    SEED = "seed"
    GOOGLE_BOOKS = "google_books"
    OPEN_LIBRARY = "open_library"
    MANUAL = "manual"


class WorkMetadataSource(StrEnum):
    LOCAL_CATALOG = "local_catalog"
    GOOGLE_BOOKS = "google_books"
    OPEN_LIBRARY = "open_library"
    MANUAL = "manual"


class WorkStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    LICENSABLE = "licensable"
    UNAVAILABLE = "unavailable"


class PermissionLevel(StrEnum):
    ACADEMIC_ONLY = "academic_only"
    COMMERCIAL_INCLUDED = "commercial_included"
    UNAVAILABLE = "unavailable"


class TermsStatus(StrEnum):
    PROPOSED = "proposed"
    REVISION_REQUESTED = "revision_requested"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REVISION_REQUESTED = "revision_requested"


class IntendedUse(StrEnum):
    ACADEMIC_NONCOMMERCIAL = "academic_noncommercial"
    COMMERCIAL = "commercial"


class RequestStatus(StrEnum):
    REQUESTED = "requested"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    AWAITING_PAYMENT = "awaiting_payment"
    ACTIVE = "active"
    EXPIRED = "expired"
    REJECTED = "rejected"


class ApprovalParty(StrEnum):
    AUTHOR = "author"
    PUBLISHER = "publisher"


class RequestDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class PaymentStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PaymentProvider(StrEnum):
    INTERNAL_SIMULATION = "internal_simulation"
    IYZICO_SANDBOX = "iyzico_sandbox"


class PaymentFailureReason(StrEnum):
    INSUFFICIENT_FUNDS = "insufficient_funds"
    INVALID_CARD = "invalid_card"
    SUSPECTED_FRAUD = "suspected_fraud"
    PROVIDER_ERROR = "provider_error"


class RecipientType(StrEnum):
    AUTHOR = "author"
    PUBLISHER = "publisher"
    PLATFORM = "platform"


class InvitationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class AiUsageType(StrEnum):
    PRETRAINING = "pretraining"
    FINE_TUNING = "fine_tuning"
    EVALUATION = "evaluation"
    OTHER = "other"