"""db guards and trgm indexes

Revision ID: 7317721f363f
Revises: dd54771cdcb2
Create Date: 2026-11-20 16:56:21.955968
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7317721f363f'
down_revision: Union[str, Sequence[str], None] = 'dd54771cdcb2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SQL_STATEMENTS_UPGRADE = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE INDEX IF NOT EXISTS ix_isbn_catalog_title_trgm ON isbn_catalog USING gin (title gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_works_title_trgm ON works USING gin (title gin_trgm_ops)",
    """
    CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger
    LANGUAGE plpgsql AS $fn$
    BEGIN
        NEW.updated_at := NOW();
        RETURN NEW;
    END;
    $fn$;
    """,
    """
    CREATE OR REPLACE FUNCTION forbid_modification() RETURNS trigger
    LANGUAGE plpgsql AS $fn$
    BEGIN
        RAISE EXCEPTION '% table is append-only: % is forbidden', TG_TABLE_NAME, TG_OP
            USING ERRCODE = 'restrict_violation';
    END;
    $fn$;
    """,
    """
    CREATE OR REPLACE FUNCTION license_terms_guard() RETURNS trigger
    LANGUAGE plpgsql AS $fn$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'license_terms rows cannot be deleted; create a new version instead'
                USING ERRCODE = 'restrict_violation';
        END IF;

        IF (NEW.id, NEW.work_id, NEW.version_no, NEW.permission_level, NEW.fee_amount,
            NEW.currency, NEW.duration_months, NEW.author_share_pct, NEW.publisher_share_pct,
            NEW.proposed_by_user_id, NEW.created_at)
           IS DISTINCT FROM
           (OLD.id, OLD.work_id, OLD.version_no, OLD.permission_level, OLD.fee_amount,
            OLD.currency, OLD.duration_months, OLD.author_share_pct, OLD.publisher_share_pct,
            OLD.proposed_by_user_id, OLD.created_at)
        THEN
            RAISE EXCEPTION 'license_terms core fields are immutable; only status and revision_note can change'
                USING ERRCODE = 'restrict_violation';
        END IF;

        IF NEW.status = OLD.status THEN
            RETURN NEW;
        END IF;

        IF (OLD.status, NEW.status) IN (
            ('proposed', 'approved'),
            ('proposed', 'revision_requested'),
            ('proposed', 'superseded'),
            ('revision_requested', 'superseded'),
            ('approved', 'superseded')
        ) THEN
            RETURN NEW;
        END IF;

        RAISE EXCEPTION 'Invalid license_terms status transition: % -> %', OLD.status, NEW.status
            USING ERRCODE = 'check_violation';
    END;
    $fn$;
    """,
    """
    CREATE OR REPLACE FUNCTION license_requests_guard() RETURNS trigger
    LANGUAGE plpgsql AS $fn$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'license_requests rows cannot be deleted'
                USING ERRCODE = 'restrict_violation';
        END IF;

        IF NEW.work_id <> OLD.work_id
           OR NEW.terms_id <> OLD.terms_id
           OR NEW.licensee_user_id <> OLD.licensee_user_id
        THEN
            RAISE EXCEPTION 'work_id, terms_id and licensee_user_id cannot be modified'
                USING ERRCODE = 'restrict_violation';
        END IF;

        IF NEW.status = OLD.status THEN
            RETURN NEW;
        END IF;

        IF (OLD.status, NEW.status) IN (
            ('requested', 'awaiting_approval'),
            ('requested', 'rejected'),
            ('awaiting_approval', 'approved'),
            ('awaiting_approval', 'rejected'),
            ('approved', 'awaiting_payment'),
            ('approved', 'active'),
            ('awaiting_payment', 'active'),
            ('active', 'expired')
        ) THEN
            RETURN NEW;
        END IF;

        RAISE EXCEPTION 'Invalid license_requests status transition: % -> %', OLD.status, NEW.status
            USING ERRCODE = 'check_violation';
    END;
    $fn$;
    """,
    """
    CREATE OR REPLACE FUNCTION payments_guard() RETURNS trigger
    LANGUAGE plpgsql AS $fn$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'payments rows cannot be deleted'
                USING ERRCODE = 'restrict_violation';
        END IF;

        IF OLD.status IN ('succeeded', 'failed') THEN
            RAISE EXCEPTION 'Finalized payment (%) cannot be modified', OLD.status
                USING ERRCODE = 'restrict_violation';
        END IF;

        IF (NEW.id, NEW.request_id, NEW.amount, NEW.currency, NEW.provider,
            NEW.idempotency_key, NEW.created_at)
           IS DISTINCT FROM
           (OLD.id, OLD.request_id, OLD.amount, OLD.currency, OLD.provider,
            OLD.idempotency_key, OLD.created_at)
        THEN
            RAISE EXCEPTION 'Payment core attributes cannot be modified'
                USING ERRCODE = 'restrict_violation';
        END IF;

        RETURN NEW;
    END;
    $fn$;
    """,
    """
    CREATE OR REPLACE FUNCTION check_revenue_total() RETURNS trigger
    LANGUAGE plpgsql AS $fn$
    DECLARE
        v_payment_id bigint;
        v_amount     numeric;
        v_status     varchar;
        v_total      numeric;
    BEGIN
        IF TG_TABLE_NAME = 'payments' THEN
            v_payment_id := NEW.id;
        ELSE
            v_payment_id := NEW.payment_id;
        END IF;

        SELECT amount, status INTO v_amount, v_status
        FROM payments WHERE id = v_payment_id;

        IF v_status = 'succeeded' THEN
            SELECT COALESCE(SUM(amount), 0) INTO v_total
            FROM revenue_shares WHERE payment_id = v_payment_id;

            IF v_total <> v_amount THEN
                RAISE EXCEPTION 'Revenue shares total (%) does not equal payment amount (%) for payment %',
                    v_total, v_amount, v_payment_id
                    USING ERRCODE = 'check_violation';
            END IF;
        END IF;

        RETURN NULL;
    END;
    $fn$;
    """,
    "CREATE TRIGGER trg_organizations_updated_at BEFORE UPDATE ON organizations FOR EACH ROW EXECUTE FUNCTION set_updated_at()",
    "CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION set_updated_at()",
    "CREATE TRIGGER trg_works_updated_at BEFORE UPDATE ON works FOR EACH ROW EXECUTE FUNCTION set_updated_at()",
    "CREATE TRIGGER trg_license_requests_updated_at BEFORE UPDATE ON license_requests FOR EACH ROW EXECUTE FUNCTION set_updated_at()",
    "CREATE TRIGGER trg_audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW EXECUTE FUNCTION forbid_modification()",
    "CREATE TRIGGER trg_audit_log_no_truncate BEFORE TRUNCATE ON audit_log FOR EACH STATEMENT EXECUTE FUNCTION forbid_modification()",
    "CREATE TRIGGER trg_revenue_shares_append_only BEFORE UPDATE OR DELETE ON revenue_shares FOR EACH ROW EXECUTE FUNCTION forbid_modification()",
    "CREATE TRIGGER trg_revenue_shares_no_truncate BEFORE TRUNCATE ON revenue_shares FOR EACH STATEMENT EXECUTE FUNCTION forbid_modification()",
    "CREATE TRIGGER trg_license_terms_guard BEFORE UPDATE OR DELETE ON license_terms FOR EACH ROW EXECUTE FUNCTION license_terms_guard()",
    "CREATE TRIGGER trg_license_requests_guard BEFORE UPDATE OR DELETE ON license_requests FOR EACH ROW EXECUTE FUNCTION license_requests_guard()",
    "CREATE TRIGGER trg_payments_guard BEFORE UPDATE OR DELETE ON payments FOR EACH ROW EXECUTE FUNCTION payments_guard()",
    "CREATE CONSTRAINT TRIGGER trg_payments_revenue_total AFTER INSERT OR UPDATE OF status, amount ON payments DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_revenue_total()",
    "CREATE CONSTRAINT TRIGGER trg_revenue_shares_total AFTER INSERT ON revenue_shares DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_revenue_total()",
]

SQL_STATEMENTS_DOWNGRADE = [
    "DROP TRIGGER IF EXISTS trg_revenue_shares_total ON revenue_shares",
    "DROP TRIGGER IF EXISTS trg_payments_revenue_total ON payments",
    "DROP TRIGGER IF EXISTS trg_payments_guard ON payments",
    "DROP TRIGGER IF EXISTS trg_license_requests_guard ON license_requests",
    "DROP TRIGGER IF EXISTS trg_license_terms_guard ON license_terms",
    "DROP TRIGGER IF EXISTS trg_revenue_shares_no_truncate ON revenue_shares",
    "DROP TRIGGER IF EXISTS trg_revenue_shares_append_only ON revenue_shares",
    "DROP TRIGGER IF EXISTS trg_audit_log_no_truncate ON audit_log",
    "DROP TRIGGER IF EXISTS trg_audit_log_append_only ON audit_log",
    "DROP TRIGGER IF EXISTS trg_license_requests_updated_at ON license_requests",
    "DROP TRIGGER IF EXISTS trg_works_updated_at ON works",
    "DROP TRIGGER IF EXISTS trg_users_updated_at ON users",
    "DROP TRIGGER IF EXISTS trg_organizations_updated_at ON organizations",
    "DROP FUNCTION IF EXISTS check_revenue_total()",
    "DROP FUNCTION IF EXISTS payments_guard()",
    "DROP FUNCTION IF EXISTS license_requests_guard()",
    "DROP FUNCTION IF EXISTS license_terms_guard()",
    "DROP FUNCTION IF EXISTS forbid_modification()",
    "DROP FUNCTION IF EXISTS set_updated_at()",
    "DROP INDEX IF EXISTS ix_works_title_trgm",
    "DROP INDEX IF EXISTS ix_isbn_catalog_title_trgm",
]


def upgrade() -> None:
    for statement in SQL_STATEMENTS_UPGRADE:
        op.execute(sa.text(statement))


def downgrade() -> None:
    for statement in SQL_STATEMENTS_DOWNGRADE:
        op.execute(sa.text(statement))