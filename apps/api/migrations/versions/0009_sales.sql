ALTER TABLE forge.inventory_documents DROP CONSTRAINT inventory_documents_type_check;
ALTER TABLE forge.inventory_documents ADD CONSTRAINT inventory_documents_type_check
 CHECK(type IN ('OPENING','ADJUSTMENT','TRANSFER','STOCKTAKE','PURCHASE_RECEIPT','PURCHASE_RETURN','SALES_RESERVATION','SALES_SHIPMENT','SALES_RETURN'));
CREATE TABLE forge.sales_orders (
 id uuid PRIMARY KEY DEFAULT uuidv7(), organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 number text NOT NULL, customer_id uuid NOT NULL, customer_name text NOT NULL,
 warehouse_id uuid NOT NULL, warehouse_name text NOT NULL, reason text NOT NULL CHECK(length(trim(reason))>0),
 status text NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT','CONFIRMED','CLOSED','CANCELLED')),
 version integer NOT NULL DEFAULT 1 CHECK(version>0), amount numeric(20,4) NOT NULL CHECK(amount>=0 AND amount<1e16),
 created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), confirmed_at timestamptz,
 closed_at timestamptz, cancelled_at timestamptz, action_reason text,
 UNIQUE(organization_id,id), UNIQUE(organization_id,number),
 FOREIGN KEY(organization_id,customer_id) REFERENCES forge.customers(organization_id,id),
 FOREIGN KEY(organization_id,warehouse_id) REFERENCES forge.warehouses(organization_id,id),
 FOREIGN KEY(organization_id,created_by) REFERENCES forge.users(organization_id,id),
 CHECK(status NOT IN ('CONFIRMED','CLOSED') OR confirmed_at IS NOT NULL),
 CHECK((status='CLOSED')=(closed_at IS NOT NULL)), CHECK((status='CANCELLED')=(cancelled_at IS NOT NULL))
);
CREATE TABLE forge.sales_order_lines (
 id uuid PRIMARY KEY DEFAULT uuidv7(), organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 order_id uuid NOT NULL, line_no integer NOT NULL, product_id uuid NOT NULL, unit_id uuid NOT NULL,
 product_label text NOT NULL, unit_label text NOT NULL, qty numeric(20,6) NOT NULL CHECK(qty>0 AND qty<1e14),
 unit_to_base_factor numeric(20,6) NOT NULL CHECK(unit_to_base_factor>0 AND unit_to_base_factor<1e14),
 base_qty numeric(20,6) NOT NULL CHECK(base_qty>0 AND base_qty<1e14), conversion_version integer NOT NULL,
 pricing_mode text NOT NULL CHECK(pricing_mode IN ('AUTO','MANUAL')),
 price_source jsonb NOT NULL CHECK(jsonb_typeof(price_source)='object'),
 unit_price numeric(20,6) NOT NULL CHECK(unit_price>=0 AND unit_price<1e14),
 amount numeric(20,4) NOT NULL CHECK(amount>=0 AND amount<1e16),
 UNIQUE(organization_id,id), UNIQUE(organization_id,order_id,id),
 UNIQUE(organization_id,order_id,product_id), UNIQUE(organization_id,order_id,line_no),
 FOREIGN KEY(organization_id,order_id) REFERENCES forge.sales_orders(organization_id,id),
 FOREIGN KEY(organization_id,product_id) REFERENCES forge.products(organization_id,id),
 FOREIGN KEY(organization_id,unit_id) REFERENCES forge.units(organization_id,id),
 CHECK(base_qty=qty*unit_to_base_factor)
);
CREATE TABLE forge.sales_documents (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL REFERENCES forge.organizations(id), order_id uuid NOT NULL,
 kind text NOT NULL CHECK(kind IN ('RESERVATION','SHIPMENT','RETURN')), original_document_id uuid,
 UNIQUE(organization_id,id), UNIQUE(organization_id,order_id,id),
 CHECK((kind='RETURN')=(original_document_id IS NOT NULL)), CHECK(id<>original_document_id),
 FOREIGN KEY(organization_id,id) REFERENCES forge.inventory_documents(organization_id,id),
 FOREIGN KEY(organization_id,order_id) REFERENCES forge.sales_orders(organization_id,id),
 FOREIGN KEY(organization_id,order_id,original_document_id) REFERENCES forge.sales_documents(organization_id,order_id,id)
);
CREATE TABLE forge.sales_document_lines (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 document_id uuid NOT NULL, order_id uuid NOT NULL, order_line_id uuid NOT NULL, shipment_line_id uuid,
 unit_price numeric(20,6) NOT NULL CHECK(unit_price>=0 AND unit_price<1e14),
 amount numeric(20,4) NOT NULL CHECK(amount>=0 AND amount<1e16),
 UNIQUE(organization_id,id), UNIQUE(organization_id,document_id,id),
 UNIQUE(organization_id,document_id,order_line_id),
 FOREIGN KEY(organization_id,document_id,id) REFERENCES forge.inventory_document_lines(organization_id,document_id,id),
 FOREIGN KEY(organization_id,order_id,document_id) REFERENCES forge.sales_documents(organization_id,order_id,id),
 FOREIGN KEY(organization_id,order_id,order_line_id) REFERENCES forge.sales_order_lines(organization_id,order_id,id),
 FOREIGN KEY(organization_id,shipment_line_id) REFERENCES forge.sales_document_lines(organization_id,id)
);
CREATE INDEX sales_order_page ON forge.sales_orders(organization_id,created_at DESC,id DESC);
CREATE INDEX sales_order_customer ON forge.sales_orders(organization_id,customer_id);
CREATE INDEX sales_document_order ON forge.sales_documents(organization_id,order_id);
CREATE INDEX sales_line_order ON forge.sales_document_lines(organization_id,order_line_id);
CREATE INDEX sales_line_receipt ON forge.sales_document_lines(organization_id,shipment_line_id);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['sales_orders','sales_order_lines','sales_documents','sales_document_lines'] LOOP
  EXECUTE format('ALTER TABLE forge.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE forge.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY tenant_scope ON forge.%I TO forge_app USING
   (organization_id=nullif(current_setting(''app.organization_id'',true),'''')::uuid)
   WITH CHECK(organization_id=nullif(current_setting(''app.organization_id'',true),'''')::uuid)',t);
  EXECUTE format('GRANT SELECT,INSERT,UPDATE ON forge.%I TO forge_app',t);
 END LOOP;
END $$;
GRANT DELETE ON forge.sales_order_lines,forge.sales_document_lines TO forge_app;
CREATE FUNCTION forge.sales_order_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Orders cannot be deleted' USING ERRCODE='23514'; END IF;
 IF NEW.id<>OLD.id OR NEW.organization_id<>OLD.organization_id OR NEW.version<>OLD.version+1 THEN
 RAISE EXCEPTION 'Order identity/version is immutable' USING ERRCODE='23514'; END IF;
 IF OLD.status IN ('CLOSED','CANCELLED') OR (OLD.status='CONFIRMED' AND
 (NEW.status NOT IN ('CLOSED','CANCELLED') OR
 (to_jsonb(NEW)-ARRAY['status','version','closed_at','cancelled_at','action_reason']) IS DISTINCT FROM
 (to_jsonb(OLD)-ARRAY['status','version','closed_at','cancelled_at','action_reason']))) THEN
 RAISE EXCEPTION 'Confirmed order content is immutable' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER sales_order_guard BEFORE UPDATE OR DELETE ON forge.sales_orders
 FOR EACH ROW EXECUTE FUNCTION forge.sales_order_guard();
CREATE FUNCTION forge.sales_line_guard() RETURNS trigger LANGUAGE plpgsql AS $$
 DECLARE rec record; s text; BEGIN
 IF TG_OP='DELETE' THEN rec:=OLD; ELSE rec:=NEW; END IF;
 IF TG_TABLE_NAME='sales_order_lines' THEN
  SELECT status INTO s FROM forge.sales_orders WHERE organization_id=rec.organization_id AND id=rec.order_id FOR UPDATE;
 ELSE
  SELECT status INTO s FROM forge.inventory_documents WHERE organization_id=rec.organization_id AND id=rec.document_id FOR UPDATE;
 END IF;
 IF s IS DISTINCT FROM 'DRAFT' THEN RAISE EXCEPTION 'Only draft sales lines can change' USING ERRCODE='23514'; END IF;
 IF TG_OP='UPDATE' THEN RAISE EXCEPTION 'Replace draft sales lines through commands' USING ERRCODE='23514'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW; END $$;
CREATE TRIGGER sales_order_line_guard BEFORE INSERT OR UPDATE OR DELETE ON forge.sales_order_lines
 FOR EACH ROW EXECUTE FUNCTION forge.sales_line_guard();
CREATE TRIGGER sales_document_line_guard BEFORE INSERT OR UPDATE OR DELETE ON forge.sales_document_lines
 FOR EACH ROW EXECUTE FUNCTION forge.sales_line_guard();
CREATE TRIGGER sales_document_immutable BEFORE UPDATE OR DELETE ON forge.sales_documents
 FOR EACH ROW EXECUTE FUNCTION forge.inventory_immutable();
REVOKE ALL ON FUNCTION forge.sales_order_guard(),forge.sales_line_guard() FROM PUBLIC;
INSERT INTO forge.permissions(code,description) VALUES
 ('sales.read','查看销售数量和单据'),('sales.order.write','编辑销售订单'),
 ('sales.order.confirm','确认销售订单'),('sales.order.cancel','取消销售订单'),
 ('sales.order.close','关闭销售订单'),('sales.ship','销售出库'),
 ('sales.return','销售退货'),('sales.reverse','销售冲销');

-- Exactly one immutable reservation document per order; physical shipments have separate lines.
CREATE UNIQUE INDEX sales_one_reservation ON forge.sales_documents(organization_id,order_id)
 WHERE kind='RESERVATION';
CREATE FUNCTION forge.sales_reservation_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF OLD.type='SALES_RESERVATION' AND OLD.status='POSTED' THEN
 RAISE EXCEPTION 'Reservation facts cannot be reversed' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER sales_reservation_guard BEFORE UPDATE ON forge.inventory_documents
 FOR EACH ROW EXECUTE FUNCTION forge.sales_reservation_guard();
REVOKE ALL ON FUNCTION forge.sales_reservation_guard() FROM PUBLIC;
