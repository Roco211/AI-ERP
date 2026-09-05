CREATE TABLE forge.replenishment_creations (
 id uuid PRIMARY KEY DEFAULT uuidv7(),
 organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 actor_id uuid NOT NULL,
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 128),
 request_hash text NOT NULL CHECK(length(request_hash)=64),
 algorithm_version text NOT NULL,
 confirmation_hash text NOT NULL CHECK(length(confirmation_hash)=64),
 basis_snapshot jsonb NOT NULL CHECK(jsonb_typeof(basis_snapshot)='object'),
 selection_snapshot jsonb NOT NULL CHECK(jsonb_typeof(selection_snapshot)='object'),
 purchase_order_id uuid NOT NULL,
 receipt jsonb NOT NULL CHECK(jsonb_typeof(receipt)='object'),
 request_id text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(organization_id,id),
 UNIQUE(organization_id,actor_id,idempotency_key),
 UNIQUE(organization_id,purchase_order_id),
 FOREIGN KEY(organization_id,actor_id) REFERENCES forge.users(organization_id,id),
 FOREIGN KEY(organization_id,purchase_order_id) REFERENCES forge.purchase_orders(organization_id,id)
);
ALTER TABLE forge.replenishment_creations ENABLE ROW LEVEL SECURITY;
ALTER TABLE forge.replenishment_creations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON forge.replenishment_creations TO forge_app
 USING(organization_id=nullif(current_setting('app.organization_id',true),'')::uuid)
 WITH CHECK(organization_id=nullif(current_setting('app.organization_id',true),'')::uuid);
GRANT SELECT,INSERT ON forge.replenishment_creations TO forge_app;
CREATE FUNCTION forge.replenishment_creation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'Replenishment creation receipts are immutable' USING ERRCODE='23514';
END $$;
REVOKE ALL ON FUNCTION forge.replenishment_creation_guard() FROM PUBLIC;
CREATE TRIGGER immutable_replenishment_creation BEFORE UPDATE OR DELETE
 ON forge.replenishment_creations FOR EACH ROW EXECUTE FUNCTION forge.replenishment_creation_guard();
INSERT INTO forge.permissions(code,description) VALUES
 ('replenishment.read','查看确定性补货建议'),
 ('replenishment.create','将已复核的补货建议创建为采购草稿');
