DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'eserlisans_app') THEN
        CREATE ROLE eserlisans_app LOGIN PASSWORD 'app_gelistirme_sifresi';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE eserlisans TO eserlisans_app;
GRANT USAGE ON SCHEMA public TO eserlisans_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO eserlisans_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO eserlisans_app;

REVOKE UPDATE, DELETE, TRUNCATE ON audit_log, revenue_shares FROM eserlisans_app;
REVOKE DELETE, TRUNCATE ON payments, license_terms, license_requests FROM eserlisans_app;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON alembic_version FROM eserlisans_app;

ALTER DEFAULT PRIVILEGES FOR ROLE eserlisans IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO eserlisans_app;
ALTER DEFAULT PRIVILEGES FOR ROLE eserlisans IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO eserlisans_app;