CREATE TABLE forge.inventory_documents (
 id uuid PRIMARY KEY DEFAULT uuidv7(), organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 number text NOT NULL, type text NOT NULL CHECK(type IN ('OPENING','ADJUSTMENT','TRANSFER','STOCKTAKE')),
 status text NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT','POSTED','REVERSED')),
 version integer NOT NULL DEFAULT 1 CHECK(version>0), reason text NOT NULL CHECK(length(reason)>0),
 warehouse_id uuid NOT NULL, target_warehouse_id uuid,
 created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 posted_at timestamptz, reversed_at timestamptz, reversal_id uuid,
 UNIQUE(organization_id,id), UNIQUE(organization_id,number),
 FOREIGN KEY(organization_id,warehouse_id) REFERENCES forge.warehouses(organization_id,id),
 FOREIGN KEY(organization_id,target_warehouse_id) REFERENCES forge.warehouses(organization_id,id),
 FOREIGN KEY(organization_id,created_by) REFERENCES forge.users(organization_id,id),
 CHECK((type='TRANSFER')=(target_warehouse_id IS NOT NULL)),
 CHECK(target_warehouse_id IS NULL OR target_warehouse_id<>warehouse_id),
 CHECK((status='DRAFT')=(posted_at IS NULL)), CHECK((status='REVERSED')=(reversal_id IS NOT NULL)),
 CHECK((status='REVERSED')=(reversed_at IS NOT NULL))
);
CREATE TABLE forge.inventory_document_lines (
 id uuid PRIMARY KEY DEFAULT uuidv7(), organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 document_id uuid NOT NULL, line_no integer NOT NULL CHECK(line_no>0), product_id uuid NOT NULL,
 unit_id uuid NOT NULL, product_label text NOT NULL, unit_label text NOT NULL,
 qty numeric(20,6) NOT NULL CHECK(qty>=0 AND qty<1e14),
 unit_to_base_factor numeric(20,6) NOT NULL CHECK(unit_to_base_factor>0 AND unit_to_base_factor<1e14),
 base_qty numeric(20,6) NOT NULL CHECK(base_qty>=0 AND base_qty<1e14),
 conversion_version integer NOT NULL, direction text NOT NULL CHECK(direction IN ('IN','OUT')),
 input_unit_cost numeric(20,6) CHECK(input_unit_cost>=0 AND input_unit_cost<1e14),
 baseline_qty numeric(20,6) NOT NULL DEFAULT 0 CHECK(baseline_qty>=0 AND baseline_qty<1e14),
 baseline_version bigint NOT NULL DEFAULT 0,
 UNIQUE(organization_id,id), UNIQUE(organization_id,document_id,product_id),
 UNIQUE(organization_id,document_id,line_no), UNIQUE(organization_id,document_id,id),
 FOREIGN KEY(organization_id,document_id) REFERENCES forge.inventory_documents(organization_id,id),
 FOREIGN KEY(organization_id,product_id) REFERENCES forge.products(organization_id,id),
 FOREIGN KEY(organization_id,unit_id) REFERENCES forge.units(organization_id,id),
 CHECK(base_qty=qty*unit_to_base_factor)
);
CREATE TABLE forge.inventory_balances (
 id uuid PRIMARY KEY DEFAULT uuidv7(), organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 warehouse_id uuid NOT NULL, product_id uuid NOT NULL,
 on_hand_qty numeric(20,6) NOT NULL DEFAULT 0 CHECK(on_hand_qty>=0 AND on_hand_qty<1e14),
 reserved_qty numeric(20,6) NOT NULL DEFAULT 0 CHECK(reserved_qty>=0 AND reserved_qty<=on_hand_qty),
 inventory_value numeric(20,4) NOT NULL DEFAULT 0 CHECK(inventory_value>=0 AND inventory_value<1e16),
 avg_unit_cost numeric(20,6) NOT NULL DEFAULT 0 CHECK(avg_unit_cost>=0 AND avg_unit_cost<1e14),
 version bigint NOT NULL DEFAULT 0 CHECK(version>=0),
 CHECK(on_hand_qty<>0 OR inventory_value=0),
 UNIQUE(organization_id,id), UNIQUE(organization_id,warehouse_id,product_id),
 FOREIGN KEY(organization_id,warehouse_id) REFERENCES forge.warehouses(organization_id,id),
 FOREIGN KEY(organization_id,product_id) REFERENCES forge.products(organization_id,id)
);
CREATE TABLE forge.inventory_reservations (
 id uuid PRIMARY KEY DEFAULT uuidv7(), organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 warehouse_id uuid NOT NULL, product_id uuid NOT NULL, document_id uuid NOT NULL, line_id uuid NOT NULL,
 reserved_qty numeric(20,6) NOT NULL DEFAULT 0 CHECK(reserved_qty>=0 AND reserved_qty<1e14),
 consumed_qty numeric(20,6) NOT NULL DEFAULT 0 CHECK(consumed_qty>=0 AND consumed_qty<1e14),
 released_qty numeric(20,6) NOT NULL DEFAULT 0 CHECK(released_qty>=0 AND released_qty<1e14),
 remaining_qty numeric(20,6) NOT NULL DEFAULT 0 CHECK(remaining_qty>=0 AND remaining_qty<1e14),
 version bigint NOT NULL DEFAULT 0,
 CHECK(reserved_qty=consumed_qty+released_qty+remaining_qty),
 UNIQUE(organization_id,id), UNIQUE(organization_id,warehouse_id,product_id,line_id),
 FOREIGN KEY(organization_id,document_id,line_id) REFERENCES forge.inventory_document_lines(organization_id,document_id,id),
 FOREIGN KEY(organization_id,warehouse_id) REFERENCES forge.warehouses(organization_id,id),
 FOREIGN KEY(organization_id,product_id) REFERENCES forge.products(organization_id,id)
);
CREATE TABLE forge.inventory_reversals (
 id uuid PRIMARY KEY DEFAULT uuidv7(), organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 document_id uuid NOT NULL, reason text NOT NULL CHECK(length(reason)>0), actor_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), request_id text NOT NULL,
 UNIQUE(organization_id,id), UNIQUE(organization_id,document_id),
 FOREIGN KEY(organization_id,document_id) REFERENCES forge.inventory_documents(organization_id,id),
 FOREIGN KEY(organization_id,actor_id) REFERENCES forge.users(organization_id,id)
);
ALTER TABLE forge.inventory_documents ADD FOREIGN KEY(organization_id,reversal_id)
 REFERENCES forge.inventory_reversals(organization_id,id);
CREATE TABLE forge.inventory_movements (
 id uuid PRIMARY KEY DEFAULT uuidv7(), organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 warehouse_id uuid NOT NULL, product_id uuid NOT NULL, sequence bigint NOT NULL CHECK(sequence>0),
 kind text NOT NULL CHECK(kind IN ('RECEIVE','ISSUE','RESERVE','RELEASE','TRANSFER_IN','TRANSFER_OUT','REVERSE')),
 base_qty numeric(20,6) NOT NULL CHECK(abs(base_qty)<1e14),
 reserved_qty_delta numeric(20,6) NOT NULL DEFAULT 0 CHECK(abs(reserved_qty_delta)<1e14),
 value_delta numeric(20,4) NOT NULL CHECK(abs(value_delta)<1e16),
 before_avg_cost numeric(20,6) NOT NULL CHECK(before_avg_cost>=0 AND before_avg_cost<1e14),
 after_avg_cost numeric(20,6) NOT NULL CHECK(after_avg_cost>=0 AND after_avg_cost<1e14),
 rounding_delta numeric(20,4) NOT NULL DEFAULT 0 CHECK(abs(rounding_delta)<1e16),
 document_id uuid NOT NULL, line_id uuid NOT NULL, operation_id uuid NOT NULL,
 reservation_id uuid, original_movement_id uuid, reversal_id uuid,
 reservation_reserved_delta numeric(20,6) NOT NULL DEFAULT 0,
 reservation_consumed_delta numeric(20,6) NOT NULL DEFAULT 0,
 reservation_released_delta numeric(20,6) NOT NULL DEFAULT 0,
 actor_id uuid NOT NULL, request_id text NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(organization_id,id), UNIQUE(organization_id,warehouse_id,product_id,sequence),
 UNIQUE(organization_id,line_id,operation_id,kind,warehouse_id), UNIQUE(organization_id,original_movement_id),
 CHECK(base_qty<>0 OR reserved_qty_delta<>0), CHECK(base_qty<>0 OR value_delta=0),
 CHECK((kind='REVERSE')=(original_movement_id IS NOT NULL)),
 CHECK((kind='REVERSE')=(reversal_id IS NOT NULL)),
 FOREIGN KEY(organization_id,warehouse_id) REFERENCES forge.warehouses(organization_id,id),
 FOREIGN KEY(organization_id,product_id) REFERENCES forge.products(organization_id,id),
 FOREIGN KEY(organization_id,document_id,line_id) REFERENCES forge.inventory_document_lines(organization_id,document_id,id),
 FOREIGN KEY(organization_id,reservation_id) REFERENCES forge.inventory_reservations(organization_id,id),
 FOREIGN KEY(organization_id,original_movement_id) REFERENCES forge.inventory_movements(organization_id,id),
 FOREIGN KEY(organization_id,reversal_id) REFERENCES forge.inventory_reversals(organization_id,id),
 FOREIGN KEY(organization_id,actor_id) REFERENCES forge.users(organization_id,id)
);
CREATE INDEX inventory_movement_document ON forge.inventory_movements(organization_id,document_id);
CREATE INDEX inventory_movement_page ON forge.inventory_movements(organization_id,created_at DESC,id DESC);
CREATE INDEX inventory_balance_product ON forge.inventory_balances(organization_id,product_id);
CREATE INDEX inventory_document_page ON forge.inventory_documents(organization_id,created_at DESC,id DESC);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['inventory_documents','inventory_document_lines','inventory_balances',
 'inventory_reservations','inventory_reversals','inventory_movements'] LOOP
  EXECUTE format('ALTER TABLE forge.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE forge.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY tenant_scope ON forge.%I TO forge_app USING
   (organization_id=nullif(current_setting(''app.organization_id'',true),'''')::uuid)
   WITH CHECK(organization_id=nullif(current_setting(''app.organization_id'',true),'''')::uuid)',t);
  EXECUTE format('GRANT SELECT,INSERT ON forge.%I TO forge_app',t);
 END LOOP;
END $$;
GRANT UPDATE ON forge.inventory_documents,forge.inventory_document_lines,forge.inventory_balances,
 forge.inventory_reservations TO forge_app;
GRANT DELETE ON forge.inventory_document_lines TO forge_app;
CREATE FUNCTION forge.inventory_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 RAISE EXCEPTION 'Inventory facts are immutable' USING ERRCODE='23514'; END $$;
CREATE TRIGGER inventory_movement_immutable BEFORE UPDATE OR DELETE ON forge.inventory_movements
 FOR EACH ROW EXECUTE FUNCTION forge.inventory_immutable();
CREATE TRIGGER inventory_reversal_immutable BEFORE UPDATE OR DELETE ON forge.inventory_reversals
 FOR EACH ROW EXECUTE FUNCTION forge.inventory_immutable();
CREATE FUNCTION forge.inventory_document_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Documents cannot be deleted' USING ERRCODE='23514'; END IF;
 IF OLD.status='REVERSED' OR (OLD.status='POSTED' AND
 (NEW.status<>'REVERSED' OR (to_jsonb(NEW)-ARRAY['status','version','reversed_at','reversal_id'])
 IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','version','reversed_at','reversal_id']))) THEN
 RAISE EXCEPTION 'Posted document is immutable' USING ERRCODE='23514'; END IF;
 IF NEW.type<>OLD.type OR NEW.organization_id<>OLD.organization_id OR NEW.id<>OLD.id THEN
 RAISE EXCEPTION 'Document identity is immutable' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER inventory_document_guard BEFORE UPDATE OR DELETE ON forge.inventory_documents
 FOR EACH ROW EXECUTE FUNCTION forge.inventory_document_guard();
CREATE FUNCTION forge.inventory_line_guard() RETURNS trigger LANGUAGE plpgsql AS $$
 DECLARE doc_status text; rec record; BEGIN
 IF TG_OP='DELETE' THEN rec:=OLD; ELSE rec:=NEW; END IF;
 SELECT status INTO doc_status FROM forge.inventory_documents WHERE organization_id=rec.organization_id
 AND id=rec.document_id FOR UPDATE;
 IF doc_status IS DISTINCT FROM 'DRAFT' THEN
 RAISE EXCEPTION 'Only draft lines can change' USING ERRCODE='23514'; END IF;
 IF TG_OP='UPDATE' AND (OLD.document_id<>NEW.document_id OR OLD.organization_id<>NEW.organization_id
 OR OLD.id<>NEW.id) THEN RAISE EXCEPTION 'Line identity is immutable' USING ERRCODE='23514'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW; END $$;
CREATE TRIGGER inventory_line_guard BEFORE INSERT OR UPDATE OR DELETE ON forge.inventory_document_lines
 FOR EACH ROW EXECUTE FUNCTION forge.inventory_line_guard();
REVOKE ALL ON FUNCTION forge.inventory_immutable(),forge.inventory_document_guard(),forge.inventory_line_guard() FROM PUBLIC;
INSERT INTO forge.permissions(code,description) VALUES
 ('inventory.read','Read stock quantities'),('product.cost.read','Read inventory costs'),
 ('inventory.opening','Create and post opening stock'),('inventory.adjust','Adjust inventory'),
 ('inventory.transfer','Transfer inventory'),('inventory.stocktake','Count inventory'),
 ('inventory.reverse','Reverse latest inventory operation'),('inventory.reconcile','Reconcile inventory')
 ON CONFLICT DO NOTHING;
