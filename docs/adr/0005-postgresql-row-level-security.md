# ADR 0005 — PostgreSQL RLS
Status: Accepted with authentication bridge requiring review

Tenant tables enable and FORCE RLS with USING and WITH CHECK for forge_app. Transaction-local set_config is executed before scoped access. Application queries also include organization_id. Composite tenant foreign keys protect associations. forge_app is not owner, superuser, createdb, createrole or bypassrls. Startup verifies its actual privileges.

Pre-authentication cannot discover a tenant through protected tables. Narrow SECURITY DEFINER SQL functions resolve a login candidate from exact organization code/email and resolve an active session from its hash. These functions have fixed pg_catalog search_path, qualified tables, no dynamic SQL and EXECUTE only for forge_app. Their owner is the elevated migration role; direct table access still observes RLS. A worker bridge returns only pending organization IDs. Review this deliberately privileged bridge before production and retain dedicated migration credentials outside the API deployment. RLS protects application SQL mistakes, not arbitrary execution using stolen app database credentials that can set a different tenant GUC.
