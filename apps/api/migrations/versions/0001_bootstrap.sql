CREATE SCHEMA forge;
REVOKE ALL ON SCHEMA forge FROM PUBLIC;
GRANT USAGE ON SCHEMA forge TO forge_app;

CREATE TABLE forge.organizations (
 id uuid PRIMARY KEY DEFAULT uuidv7(),
 code text NOT NULL UNIQUE CHECK (code = upper(code)),
 name text NOT NULL,
 active boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE forge.users (
 id uuid PRIMARY KEY DEFAULT uuidv7(),
 organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 email text NOT NULL CHECK (email = lower(email)),
 display_name text NOT NULL,
 password_hash text NOT NULL,
 active boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(organization_id, email), UNIQUE(organization_id, id)
);
CREATE TABLE forge.permissions (code text PRIMARY KEY, description text NOT NULL);
INSERT INTO forge.permissions VALUES ('profile.read', 'Read own profile'),
 ('system.read', 'Read platform version');
CREATE TABLE forge.roles (
 id uuid PRIMARY KEY DEFAULT uuidv7(),
 organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 code text NOT NULL, name text NOT NULL,
 UNIQUE(organization_id, code), UNIQUE(organization_id, id)
);
CREATE TABLE forge.user_roles (
 organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 user_id uuid NOT NULL, role_id uuid NOT NULL,
 PRIMARY KEY(organization_id, user_id, role_id),
 FOREIGN KEY(organization_id, user_id) REFERENCES forge.users(organization_id, id),
 FOREIGN KEY(organization_id, role_id) REFERENCES forge.roles(organization_id, id)
);
CREATE TABLE forge.role_permissions (
 organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 role_id uuid NOT NULL, permission_code text NOT NULL REFERENCES forge.permissions(code),
 PRIMARY KEY(organization_id, role_id, permission_code),
 FOREIGN KEY(organization_id, role_id) REFERENCES forge.roles(organization_id, id)
);
CREATE TABLE forge.sessions (
 id uuid PRIMARY KEY DEFAULT uuidv7(),
 organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 user_id uuid NOT NULL,
 token_hash text NOT NULL UNIQUE CHECK(length(token_hash)=64),
 expires_at timestamptz NOT NULL,
 revoked_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(organization_id, user_id) REFERENCES forge.users(organization_id, id)
);
CREATE INDEX sessions_expiry ON forge.sessions(expires_at);
CREATE TABLE forge.audit_events (
 id uuid PRIMARY KEY DEFAULT uuidv7(),
 organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 actor_type text NOT NULL CHECK(actor_type IN ('USER','SYSTEM')),
 actor_id uuid,
 action text NOT NULL, resource_type text NOT NULL, resource_id uuid,
 before jsonb, after jsonb,
 request_id text NOT NULL,
 source text NOT NULL CHECK(source IN ('WEB','AI','API','SYSTEM')),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_tenant_created ON forge.audit_events(organization_id, created_at);
CREATE TABLE forge.outbox_events (
 id uuid PRIMARY KEY DEFAULT uuidv7(),
 organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 event_type text NOT NULL, payload jsonb NOT NULL,
 request_id text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 processed_at timestamptz,
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts >= 0)
);
CREATE INDEX outbox_pending ON forge.outbox_events(organization_id, created_at)
 WHERE processed_at IS NULL;
CREATE TABLE forge.idempotency_keys (
 organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 actor_id uuid NOT NULL,
 operation text NOT NULL, key text NOT NULL CHECK(length(key) BETWEEN 8 AND 128),
 request_hash text NOT NULL,
 response jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 expires_at timestamptz NOT NULL DEFAULT now() + interval '24 hours',
 PRIMARY KEY(organization_id, actor_id, operation, key)
);

ALTER TABLE forge.organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE forge.organizations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON forge.organizations TO forge_app
 USING(id = nullif(current_setting('app.organization_id', true), '')::uuid)
 WITH CHECK(id = nullif(current_setting('app.organization_id', true), '')::uuid);
DO $policy$
DECLARE t text;
BEGIN
 FOREACH t IN ARRAY ARRAY['users','roles','user_roles','role_permissions','sessions',
 'audit_events','outbox_events','idempotency_keys'] LOOP
  EXECUTE format('ALTER TABLE forge.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE forge.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY tenant_scope ON forge.%I TO forge_app
   USING (organization_id = nullif(current_setting(''app.organization_id'', true), '''')::uuid)
   WITH CHECK (organization_id = nullif(current_setting(''app.organization_id'', true), '''')::uuid)',t);
 END LOOP;
END $policy$;
GRANT SELECT ON ALL TABLES IN SCHEMA forge TO forge_app;
GRANT INSERT, UPDATE, DELETE ON forge.sessions, forge.idempotency_keys TO forge_app;
GRANT INSERT ON forge.audit_events TO forge_app;
GRANT INSERT, UPDATE ON forge.outbox_events TO forge_app;

-- Narrow pre-authentication bridges. Fixed search_path, qualified tables, no dynamic SQL.
CREATE FUNCTION forge.login_candidate(org_code text, user_email text)
RETURNS TABLE(organization_id uuid, user_id uuid, password_hash text)
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog
AS $$ SELECT o.id, u.id, u.password_hash FROM forge.organizations o
 JOIN forge.users u ON u.organization_id=o.id
 WHERE o.code=org_code AND u.email=user_email AND o.active AND u.active $$;
CREATE FUNCTION forge.resolve_session(session_hash text)
RETURNS TABLE(organization_id uuid, user_id uuid)
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog
AS $$ SELECT s.organization_id, s.user_id FROM forge.sessions s
 JOIN forge.users u ON (u.organization_id,u.id)=(s.organization_id,s.user_id)
 JOIN forge.organizations o ON o.id=s.organization_id
 WHERE s.token_hash=session_hash AND s.revoked_at IS NULL
 AND s.expires_at > now() AND u.active AND o.active $$;
-- Worker sees only tenant IDs with pending events; event reads still use tenant RLS.
CREATE FUNCTION forge.pending_outbox_tenants()
RETURNS TABLE(organization_id uuid)
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog
AS $$ SELECT DISTINCT e.organization_id FROM forge.outbox_events e
 WHERE e.processed_at IS NULL $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA forge FROM PUBLIC;
GRANT EXECUTE ON FUNCTION forge.login_candidate(text,text),
 forge.resolve_session(text), forge.pending_outbox_tenants() TO forge_app;
