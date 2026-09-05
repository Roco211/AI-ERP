CREATE TABLE forge.ai_provider_settings (
 organization_id uuid PRIMARY KEY REFERENCES forge.organizations(id),
 name text NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
 base_url text NOT NULL CHECK(length(base_url) BETWEEN 1 AND 2048),
 model text NOT NULL CHECK(length(model) BETWEEN 1 AND 160),
 enabled boolean NOT NULL DEFAULT false,
 allow_private_network boolean NOT NULL DEFAULT false,
 encrypted_key text CHECK(encrypted_key IS NULL OR length(encrypted_key)<=4096),
 version integer NOT NULL DEFAULT 1 CHECK(version>=1),
 updated_by uuid NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 test_started_at timestamptz,
 FOREIGN KEY(organization_id,updated_by) REFERENCES forge.users(organization_id,id)
);
ALTER TABLE forge.ai_provider_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE forge.ai_provider_settings FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON forge.ai_provider_settings TO forge_app
 USING(organization_id=nullif(current_setting('app.organization_id',true),'')::uuid)
 WITH CHECK(organization_id=nullif(current_setting('app.organization_id',true),'')::uuid);
GRANT SELECT,INSERT,UPDATE ON forge.ai_provider_settings TO forge_app;

-- Safe recovery after a browser refresh uses the original saved message and selection.
ALTER TABLE forge.assistant_turns ADD COLUMN request_body jsonb NOT NULL DEFAULT '{}'
 CHECK(jsonb_typeof(request_body)='object' AND octet_length(request_body::text)<=16384);
ALTER TABLE forge.assistant_turns ADD COLUMN resolved_day date;

CREATE UNIQUE INDEX assistant_proposal_turn_unique
 ON forge.assistant_proposals(organization_id,owner_id,turn_id);
