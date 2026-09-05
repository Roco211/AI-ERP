CREATE TABLE forge.import_batches (
 id uuid PRIMARY KEY DEFAULT uuidv7(), organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 resource text NOT NULL CHECK(resource IN ('categories','brands','units','customers','suppliers',
 'warehouses','products','product-units','product-prices','supplier-products')),
 mode text NOT NULL CHECK(mode IN ('CREATE_ONLY','UPDATE_EXISTING')),
 filename text NOT NULL, file_hash text NOT NULL CHECK(length(file_hash)=64), worksheet text NOT NULL,
 preview_hash text NOT NULL CHECK(length(preview_hash)=64),
 header_order text[] NOT NULL CHECK(cardinality(header_order) BETWEEN 1 AND 64), created_by uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '7 days',
 version integer NOT NULL DEFAULT 1 CHECK(version>0), confirmed_at timestamptz,
 confirmation_key text, confirmation_hash text, confirmation_request_id text,
 body_purged_at timestamptz,
 CHECK((confirmed_at IS NULL)=(confirmation_key IS NULL)),
 CHECK((confirmed_at IS NULL)=(confirmation_hash IS NULL)),
 UNIQUE(organization_id,id), FOREIGN KEY(organization_id,created_by) REFERENCES forge.users(organization_id,id)
);
CREATE TABLE forge.import_rows (
 id uuid PRIMARY KEY DEFAULT uuidv7(), organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 batch_id uuid NOT NULL, row_no integer NOT NULL CHECK(row_no>=2),
 action text NOT NULL CHECK(action IN ('CREATE','UPDATE')),
 status text NOT NULL CHECK(status IN ('READY','INVALID','PENDING','SUCCEEDED','FAILED')),
 content_hash text NOT NULL CHECK(length(content_hash)=64), raw_values jsonb,
 cleaned_values jsonb, command_values jsonb, references_snapshot jsonb, locator jsonb,
 expected_target_id uuid, expected_version integer, target_id uuid, target_version integer,
 errors jsonb, error_code text, retryable boolean NOT NULL DEFAULT false,
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0), request_id text,
 completed_at timestamptz, body_purged_at timestamptz,
 CHECK((status='SUCCEEDED')=(target_id IS NOT NULL)),
 CHECK(status<>'SUCCEEDED' OR (target_version IS NOT NULL AND target_version>0 AND completed_at IS NOT NULL)),
 CHECK(status NOT IN ('PENDING','SUCCEEDED') OR action<>'UPDATE' OR
  (expected_target_id IS NOT NULL AND expected_version IS NOT NULL AND expected_version>0)),
 UNIQUE(organization_id,id), UNIQUE(organization_id,batch_id,row_no),
 FOREIGN KEY(organization_id,batch_id) REFERENCES forge.import_batches(organization_id,id)
);
CREATE INDEX import_pending ON forge.import_rows(organization_id,batch_id,row_no) WHERE status='PENDING';
CREATE INDEX import_blocked ON forge.import_rows(organization_id,batch_id)
 WHERE status='FAILED' AND error_code IN ('PERMISSION_DENIED','IMPORT_ACTOR_INACTIVE');
CREATE INDEX import_batch_page ON forge.import_batches(organization_id,created_at DESC,id DESC);
CREATE FUNCTION forge.import_batch_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Import batches retain permanent receipts' USING ERRCODE='23514'; END IF;
 IF (NEW.id,NEW.organization_id,NEW.resource,NEW.mode,NEW.filename,NEW.file_hash,NEW.worksheet,
     NEW.preview_hash,NEW.header_order,NEW.created_by,NEW.created_at,NEW.expires_at)
 IS DISTINCT FROM (OLD.id,OLD.organization_id,OLD.resource,OLD.mode,OLD.filename,OLD.file_hash,
     OLD.worksheet,OLD.preview_hash,OLD.header_order,OLD.created_by,OLD.created_at,OLD.expires_at)
 THEN RAISE EXCEPTION 'Import preview identity is immutable' USING ERRCODE='23514'; END IF;
 IF OLD.confirmed_at IS NOT NULL AND
 (NEW.confirmed_at,NEW.confirmation_key,NEW.confirmation_hash,NEW.confirmation_request_id)
 IS DISTINCT FROM (OLD.confirmed_at,OLD.confirmation_key,OLD.confirmation_hash,OLD.confirmation_request_id)
 THEN RAISE EXCEPTION 'Confirmed import intent is immutable' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION forge.import_row_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Import rows retain permanent receipts' USING ERRCODE='23514'; END IF;
 IF (NEW.id,NEW.organization_id,NEW.batch_id,NEW.row_no,NEW.action,NEW.content_hash,
     NEW.expected_target_id,NEW.expected_version)
 IS DISTINCT FROM (OLD.id,OLD.organization_id,OLD.batch_id,OLD.row_no,OLD.action,OLD.content_hash,
     OLD.expected_target_id,OLD.expected_version)
 THEN RAISE EXCEPTION 'Import row intent is immutable' USING ERRCODE='23514'; END IF;
 IF (NEW.raw_values,NEW.cleaned_values,NEW.command_values,NEW.references_snapshot,NEW.locator)
 IS DISTINCT FROM (OLD.raw_values,OLD.cleaned_values,OLD.command_values,OLD.references_snapshot,OLD.locator)
 AND NOT (NEW.raw_values IS NULL AND NEW.cleaned_values IS NULL AND NEW.command_values IS NULL
     AND NEW.references_snapshot IS NULL AND NEW.locator IS NULL AND NEW.body_purged_at IS NOT NULL
     AND EXISTS(SELECT 1 FROM forge.import_batches b WHERE b.organization_id=OLD.organization_id
       AND b.id=OLD.batch_id AND b.expires_at<=clock_timestamp()))
 THEN RAISE EXCEPTION 'Import row content is frozen until retention cleanup' USING ERRCODE='23514'; END IF;
 IF OLD.status='SUCCEEDED' AND
 (NEW.status,NEW.target_id,NEW.target_version,NEW.completed_at,NEW.request_id,NEW.attempts)
 IS DISTINCT FROM (OLD.status,OLD.target_id,OLD.target_version,OLD.completed_at,OLD.request_id,OLD.attempts)
 THEN RAISE EXCEPTION 'Successful import receipt is permanent' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['import_batches','import_rows'] LOOP
  EXECUTE format('ALTER TABLE forge.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE forge.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY tenant_scope ON forge.%I TO forge_app USING
   (organization_id=nullif(current_setting(''app.organization_id'',true),'''')::uuid)
   WITH CHECK(organization_id=nullif(current_setting(''app.organization_id'',true),'''')::uuid)',t);
  EXECUTE format('GRANT SELECT,INSERT,UPDATE ON forge.%I TO forge_app',t);
 END LOOP;
END $$;
CREATE TRIGGER import_batch_guard BEFORE UPDATE OR DELETE ON forge.import_batches
 FOR EACH ROW EXECUTE FUNCTION forge.import_batch_guard();
CREATE TRIGGER import_row_guard BEFORE UPDATE OR DELETE ON forge.import_rows
 FOR EACH ROW EXECUTE FUNCTION forge.import_row_guard();
-- Discovery returns identifiers only; all data access and every write use ordinary tenant RLS.
CREATE FUNCTION forge.pending_import_tenants() RETURNS TABLE(organization_id uuid)
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT DISTINCT b.organization_id FROM forge.import_batches b WHERE
 (b.expires_at<=clock_timestamp() AND b.body_purged_at IS NULL) OR
 (b.confirmed_at IS NOT NULL AND b.expires_at>clock_timestamp() AND
  EXISTS(SELECT 1 FROM forge.import_rows r WHERE r.organization_id=b.organization_id
   AND r.batch_id=b.id AND r.status='PENDING') AND NOT EXISTS
  (SELECT 1 FROM forge.import_rows r WHERE r.organization_id=b.organization_id AND r.batch_id=b.id
   AND r.status='FAILED' AND r.error_code IN ('PERMISSION_DENIED','IMPORT_ACTOR_INACTIVE')))
 $$;
REVOKE ALL ON FUNCTION forge.pending_import_tenants(),forge.import_batch_guard(),forge.import_row_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION forge.pending_import_tenants() TO forge_app;
INSERT INTO forge.permissions(code,description) VALUES
 ('catalog.import.read','查看资料导入预览与结果'),('catalog.import.write','确认和重试本人资料导入');
