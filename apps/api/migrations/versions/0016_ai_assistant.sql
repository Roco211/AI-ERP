-- AI state is private progress, never authorization or a second business fact store.
CREATE TABLE forge.assistant_conversations (
 id uuid PRIMARY KEY DEFAULT uuidv7(),
 organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 owner_id uuid NOT NULL,
 permissions_hash text NOT NULL CHECK(length(permissions_hash)=64),
 title text NOT NULL DEFAULT '新对话' CHECK(length(title)<=120),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '7 days',
 deleted_at timestamptz,
 UNIQUE(organization_id,owner_id,id),
 FOREIGN KEY(organization_id,owner_id) REFERENCES forge.users(organization_id,id)
);
CREATE INDEX assistant_conversations_owner ON forge.assistant_conversations
 (organization_id,owner_id,created_at DESC,id);
CREATE INDEX assistant_conversations_expiry ON forge.assistant_conversations(expires_at,id);

CREATE TABLE forge.assistant_turns (
 id uuid PRIMARY KEY DEFAULT uuidv7(),
 organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 owner_id uuid NOT NULL,
 conversation_id uuid NOT NULL,
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 128),
 request_hash text NOT NULL CHECK(length(request_hash)=64),
 prompt text NOT NULL CHECK(length(prompt)<=4000),
 kind text NOT NULL DEFAULT 'CHAT' CHECK(kind IN ('CHAT','BRIEF')),
 state text NOT NULL DEFAULT 'RUNNING' CHECK(state IN ('RUNNING','WAITING','COMPLETED','FAILED')),
 lease_until timestamptz,
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),
 model_calls integer NOT NULL DEFAULT 0 CHECK(model_calls BETWEEN 0 AND 5),
 tool_calls integer NOT NULL DEFAULT 0 CHECK(tool_calls BETWEEN 0 AND 8),
 response jsonb CHECK(response IS NULL OR
   (jsonb_typeof(response)='object' AND octet_length(response::text)<=1048576)),
 error_code text CHECK(error_code IS NULL OR length(error_code)<=120),
 request_id text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '7 days',
 UNIQUE(organization_id,owner_id,id),
 UNIQUE(organization_id,owner_id,conversation_id,id),
 UNIQUE(organization_id,owner_id,conversation_id,idempotency_key),
 FOREIGN KEY(organization_id,owner_id,conversation_id)
   REFERENCES forge.assistant_conversations(organization_id,owner_id,id)
);
CREATE INDEX assistant_turns_conversation ON forge.assistant_turns
 (organization_id,owner_id,conversation_id,created_at,id);
CREATE INDEX assistant_turns_expiry ON forge.assistant_turns(expires_at,id);

CREATE TABLE forge.assistant_proposals (
 id uuid PRIMARY KEY DEFAULT uuidv7(),
 organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 owner_id uuid NOT NULL,
 conversation_id uuid NOT NULL,
 turn_id uuid NOT NULL,
 revision integer NOT NULL DEFAULT 1 CHECK(revision>=1),
 kind text NOT NULL CHECK(kind IN ('SALES','PURCHASE')),
 body jsonb NOT NULL CHECK(jsonb_typeof(body)='object' AND octet_length(body::text)<=1048576),
 preview jsonb NOT NULL CHECK(jsonb_typeof(preview)='object' AND octet_length(preview::text)<=1048576),
 confirmation_hash text NOT NULL CHECK(length(confirmation_hash)=64),
 status text NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','CREATED','REJECTED','EXPIRED')),
 expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '30 minutes',
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(organization_id,owner_id,id),
 UNIQUE(organization_id,owner_id,conversation_id,turn_id,id),
 FOREIGN KEY(organization_id,owner_id,conversation_id,turn_id)
   REFERENCES forge.assistant_turns(organization_id,owner_id,conversation_id,id)
);
CREATE INDEX assistant_proposals_turn ON forge.assistant_proposals
 (organization_id,owner_id,conversation_id,turn_id,id);
CREATE INDEX assistant_proposals_expiry ON forge.assistant_proposals(expires_at,id)
 WHERE status='PENDING';

CREATE TABLE forge.assistant_draft_receipts (
 id uuid PRIMARY KEY DEFAULT uuidv7(),
 organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 owner_id uuid NOT NULL,
 conversation_id uuid NOT NULL,
 turn_id uuid NOT NULL,
 proposal_id uuid NOT NULL,
 revision integer NOT NULL CHECK(revision>=1),
 confirmation_hash text NOT NULL CHECK(length(confirmation_hash)=64),
 kind text NOT NULL CHECK(kind IN ('SALES','PURCHASE')),
 sales_order_id uuid,
 purchase_order_id uuid,
 receipt jsonb NOT NULL CHECK(jsonb_typeof(receipt)='object' AND octet_length(receipt::text)<=1048576),
 request_id text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(organization_id,proposal_id),
 CHECK((kind='SALES' AND sales_order_id IS NOT NULL AND purchase_order_id IS NULL)
    OR (kind='PURCHASE' AND purchase_order_id IS NOT NULL AND sales_order_id IS NULL)),
 FOREIGN KEY(organization_id,owner_id,conversation_id,turn_id,proposal_id)
   REFERENCES forge.assistant_proposals(organization_id,owner_id,conversation_id,turn_id,id),
 FOREIGN KEY(organization_id,sales_order_id) REFERENCES forge.sales_orders(organization_id,id),
 FOREIGN KEY(organization_id,purchase_order_id) REFERENCES forge.purchase_orders(organization_id,id)
);
CREATE INDEX assistant_receipts_owner ON forge.assistant_draft_receipts
 (organization_id,owner_id,conversation_id,created_at,id);
CREATE FUNCTION forge.assistant_receipt_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'AI draft creation receipts are immutable' USING ERRCODE='23514';
END $$;
REVOKE ALL ON FUNCTION forge.assistant_receipt_guard() FROM PUBLIC;
CREATE TRIGGER immutable_assistant_receipt BEFORE UPDATE OR DELETE
 ON forge.assistant_draft_receipts FOR EACH ROW EXECUTE FUNCTION forge.assistant_receipt_guard();

-- The application binds one root graph to one turn. Pending writes may commit before
-- their checkpoint in LangGraph's async durability mode; both are bound to the same
-- authorized turn, without a checkpoint FK that would race framework persistence.
CREATE TABLE forge.assistant_checkpoints (
 organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 owner_id uuid NOT NULL,
 conversation_id uuid NOT NULL,
 turn_id uuid NOT NULL,
 checkpoint_ns text NOT NULL DEFAULT '' CHECK(checkpoint_ns=''),
 checkpoint_id text NOT NULL CHECK(length(checkpoint_id) BETWEEN 1 AND 128),
 parent_checkpoint_id text CHECK(parent_checkpoint_id IS NULL OR
   length(parent_checkpoint_id) BETWEEN 1 AND 128),
 checkpoint jsonb NOT NULL CHECK(jsonb_typeof(checkpoint)='object' AND
   octet_length(checkpoint::text)<=1048576),
 metadata jsonb NOT NULL CHECK(jsonb_typeof(metadata)='object' AND
   octet_length(metadata::text)<=16384),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(organization_id,owner_id,conversation_id,turn_id,checkpoint_ns,checkpoint_id),
 FOREIGN KEY(organization_id,owner_id,conversation_id,turn_id)
   REFERENCES forge.assistant_turns(organization_id,owner_id,conversation_id,id)
);
CREATE TABLE forge.assistant_checkpoint_writes (
 organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 owner_id uuid NOT NULL,
 conversation_id uuid NOT NULL,
 turn_id uuid NOT NULL,
 checkpoint_ns text NOT NULL DEFAULT '' CHECK(checkpoint_ns=''),
 checkpoint_id text NOT NULL CHECK(length(checkpoint_id) BETWEEN 1 AND 128),
 task_id text NOT NULL CHECK(length(task_id) BETWEEN 1 AND 128),
 task_path text NOT NULL DEFAULT '' CHECK(length(task_path)<=1024),
 write_idx integer NOT NULL CHECK(write_idx BETWEEN -4 AND 127),
 channel text NOT NULL CHECK(length(channel) BETWEEN 1 AND 256),
 value jsonb NOT NULL CHECK(jsonb_typeof(value)='object' AND octet_length(value::text)<=1048576),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(organization_id,owner_id,conversation_id,turn_id,checkpoint_ns,checkpoint_id,task_id,write_idx),
 FOREIGN KEY(organization_id,owner_id,conversation_id,turn_id)
   REFERENCES forge.assistant_turns(organization_id,owner_id,conversation_id,id)
);

DO $$
DECLARE table_name text;
BEGIN
 FOREACH table_name IN ARRAY ARRAY[
  'assistant_conversations','assistant_turns','assistant_proposals','assistant_draft_receipts',
  'assistant_checkpoints','assistant_checkpoint_writes'
 ] LOOP
  EXECUTE format('ALTER TABLE forge.%I ENABLE ROW LEVEL SECURITY',table_name);
  EXECUTE format('ALTER TABLE forge.%I FORCE ROW LEVEL SECURITY',table_name);
  EXECUTE format('CREATE POLICY owner_scope ON forge.%I TO forge_app USING('
   'organization_id=nullif(current_setting(''app.organization_id'',true),'''')::uuid AND '
   'owner_id=nullif(current_setting(''app.user_id'',true),'''')::uuid) WITH CHECK('
   'organization_id=nullif(current_setting(''app.organization_id'',true),'''')::uuid AND '
   'owner_id=nullif(current_setting(''app.user_id'',true),'''')::uuid)',table_name);
 END LOOP;
END $$;
GRANT SELECT,INSERT,UPDATE ON forge.assistant_conversations,forge.assistant_turns,
 forge.assistant_proposals TO forge_app;
GRANT SELECT,INSERT ON forge.assistant_draft_receipts TO forge_app;
GRANT SELECT,INSERT,UPDATE,DELETE ON forge.assistant_checkpoints,
 forge.assistant_checkpoint_writes TO forge_app;
INSERT INTO forge.permissions(code,description) VALUES
 ('ai.use','使用本人权限内的 ERP 助手与每日简报'),
 ('ai.draft.create','复核 AI 提案并创建业务草稿'),
 ('ai.provider.manage','管理组织的 AI 模型服务配置');
