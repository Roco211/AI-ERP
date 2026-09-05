-- No historical commercial document is assumed unpaid during an upgrade.
CREATE TABLE forge.funds_activation (
 id uuid PRIMARY KEY DEFAULT uuidv7(), organization_id uuid NOT NULL UNIQUE REFERENCES forge.organizations(id),
 business_date date NOT NULL, reason text NOT NULL CHECK(length(trim(reason))>0),
 created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(organization_id,id),
 FOREIGN KEY(organization_id,created_by) REFERENCES forge.users(organization_id,id)
);
CREATE TABLE forge.funds_sources (
 id uuid PRIMARY KEY DEFAULT uuidv7(), organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 number text NOT NULL, side text NOT NULL CHECK(side IN ('AR','AP')), party_id uuid NOT NULL,
 customer_id uuid, supplier_id uuid, party_name text NOT NULL,
 kind text NOT NULL CHECK(kind IN ('SHIPMENT','RECEIPT','OPENING','LEGACY','ADJUSTMENT')),
 source_document_id uuid, source_document_number text,
 commercial_amount numeric(20,4) NOT NULL CHECK(commercial_amount>=0 AND commercial_amount<1e16),
 business_date date NOT NULL, reason text NOT NULL CHECK(length(trim(reason))>0),
 legacy_snapshot jsonb NOT NULL DEFAULT '{}', created_by uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK((side='AR' AND customer_id IS NOT NULL AND customer_id=party_id AND supplier_id IS NULL) OR
       (side='AP' AND supplier_id IS NOT NULL AND supplier_id=party_id AND customer_id IS NULL)),
 CHECK(kind<>'SHIPMENT' OR side='AR'), CHECK(kind<>'RECEIPT' OR side='AP'),
 CHECK((kind IN ('SHIPMENT','RECEIPT','LEGACY'))=(source_document_id IS NOT NULL)),
 UNIQUE(organization_id,id), UNIQUE(organization_id,id,side,party_id),
 UNIQUE(organization_id,number), UNIQUE(organization_id,source_document_id),
 FOREIGN KEY(organization_id) REFERENCES forge.funds_activation(organization_id),
 FOREIGN KEY(organization_id,customer_id) REFERENCES forge.customers(organization_id,id),
 FOREIGN KEY(organization_id,supplier_id) REFERENCES forge.suppliers(organization_id,id),
 FOREIGN KEY(organization_id,source_document_id) REFERENCES forge.inventory_documents(organization_id,id),
 FOREIGN KEY(organization_id,created_by) REFERENCES forge.users(organization_id,id)
);
CREATE TABLE forge.funds_entries (
 id uuid PRIMARY KEY DEFAULT uuidv7(), organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 source_id uuid NOT NULL,
 kind text NOT NULL CHECK(kind IN ('ORIGIN','RETURN','DOCUMENT_REVERSE','TRANSFER_OUT','VOID')),
 amount numeric(20,4) NOT NULL CHECK(abs(amount)<1e16),
 document_id uuid, related_source_id uuid, reverses_id uuid,
 reason text NOT NULL CHECK(length(trim(reason))>0), created_by uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(organization_id,id), UNIQUE(organization_id,document_id,kind),
 UNIQUE(organization_id,reverses_id),
 FOREIGN KEY(organization_id,source_id) REFERENCES forge.funds_sources(organization_id,id),
 FOREIGN KEY(organization_id,related_source_id) REFERENCES forge.funds_sources(organization_id,id),
 FOREIGN KEY(organization_id,document_id) REFERENCES forge.inventory_documents(organization_id,id),
 FOREIGN KEY(organization_id,reverses_id) REFERENCES forge.funds_entries(organization_id,id),
 FOREIGN KEY(organization_id,created_by) REFERENCES forge.users(organization_id,id)
);
CREATE UNIQUE INDEX funds_source_origin ON forge.funds_entries(organization_id,source_id) WHERE kind='ORIGIN';
CREATE UNIQUE INDEX funds_source_void ON forge.funds_entries(organization_id,source_id) WHERE kind='VOID';
CREATE TABLE forge.funds_cash_documents (
 id uuid PRIMARY KEY DEFAULT uuidv7(), organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
 number text NOT NULL, side text NOT NULL CHECK(side IN ('AR','AP')), party_id uuid NOT NULL,
 customer_id uuid, supplier_id uuid, party_name text NOT NULL,
 kind text NOT NULL CHECK(kind IN ('SETTLEMENT','REFUND')),
 amount numeric(20,4) NOT NULL CHECK(amount>0 AND amount<1e16), business_date date NOT NULL,
 method text NOT NULL CHECK(method IN ('CASH','BANK_TRANSFER','OTHER')),
 external_reference text NOT NULL, reason text NOT NULL CHECK(length(trim(reason))>0),
 created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK((side='AR' AND customer_id IS NOT NULL AND customer_id=party_id AND supplier_id IS NULL) OR
       (side='AP' AND supplier_id IS NOT NULL AND supplier_id=party_id AND customer_id IS NULL)),
 UNIQUE(organization_id,id), UNIQUE(organization_id,id,side,party_id), UNIQUE(organization_id,number),
 FOREIGN KEY(organization_id) REFERENCES forge.funds_activation(organization_id),
 FOREIGN KEY(organization_id,customer_id) REFERENCES forge.customers(organization_id,id),
 FOREIGN KEY(organization_id,supplier_id) REFERENCES forge.suppliers(organization_id,id),
 FOREIGN KEY(organization_id,created_by) REFERENCES forge.users(organization_id,id)
);
CREATE TABLE forge.funds_cash_allocations (
 id uuid PRIMARY KEY DEFAULT uuidv7(), organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 cash_id uuid NOT NULL, source_id uuid NOT NULL, side text NOT NULL, party_id uuid NOT NULL,
 amount numeric(20,4) NOT NULL CHECK(amount>0 AND amount<1e16),
 UNIQUE(organization_id,id), UNIQUE(organization_id,cash_id,source_id),
 FOREIGN KEY(organization_id,cash_id,side,party_id) REFERENCES forge.funds_cash_documents(organization_id,id,side,party_id),
 FOREIGN KEY(organization_id,source_id,side,party_id) REFERENCES forge.funds_sources(organization_id,id,side,party_id)
);
CREATE TABLE forge.funds_cash_reversals (
 id uuid PRIMARY KEY DEFAULT uuidv7(), organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 cash_id uuid NOT NULL, reason text NOT NULL CHECK(length(trim(reason))>0), created_by uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(organization_id,id), UNIQUE(organization_id,cash_id),
 FOREIGN KEY(organization_id,cash_id) REFERENCES forge.funds_cash_documents(organization_id,id),
 FOREIGN KEY(organization_id,created_by) REFERENCES forge.users(organization_id,id)
);
-- Permanent command receipts prevent duplicate financial facts after ordinary idempotency expiry.
CREATE TABLE forge.funds_operations (
 id uuid PRIMARY KEY DEFAULT uuidv7(), organization_id uuid NOT NULL REFERENCES forge.organizations(id),
 actor_id uuid NOT NULL, operation text NOT NULL, key text NOT NULL,
 request_hash text NOT NULL, response jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(organization_id,id), UNIQUE(organization_id,actor_id,operation,key),
 FOREIGN KEY(organization_id,actor_id) REFERENCES forge.users(organization_id,id)
);
CREATE INDEX funds_source_party ON forge.funds_sources(organization_id,side,party_id,created_at DESC,id DESC);
CREATE INDEX funds_entry_source ON forge.funds_entries(organization_id,source_id);
CREATE INDEX funds_cash_party ON forge.funds_cash_documents(organization_id,side,party_id,created_at DESC,id DESC);
CREATE INDEX funds_allocation_source ON forge.funds_cash_allocations(organization_id,source_id);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['funds_activation','funds_sources','funds_entries','funds_cash_documents',
                         'funds_cash_allocations','funds_cash_reversals','funds_operations'] LOOP
  EXECUTE format('ALTER TABLE forge.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE forge.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY tenant_scope ON forge.%I TO forge_app USING
   (organization_id=nullif(current_setting(''app.organization_id'',true),'''')::uuid)
   WITH CHECK(organization_id=nullif(current_setting(''app.organization_id'',true),'''')::uuid)',t);
  EXECUTE format('GRANT SELECT,INSERT ON forge.%I TO forge_app',t);
  EXECUTE format('CREATE TRIGGER funds_immutable BEFORE UPDATE OR DELETE ON forge.%I
   FOR EACH ROW EXECUTE FUNCTION forge.inventory_immutable()',t);
 END LOOP;
END $$;
INSERT INTO forge.permissions(code,description) VALUES
 ('funds.ar.read','查看应收与客户资金往来'),('funds.ap.read','查看应付与供应商资金往来'),
 ('funds.receive','登记客户收款'),('funds.pay','登记供应商付款'),
 ('funds.customer_refund','登记退给客户的款项'),('funds.supplier_refund','登记供应商退回的款项'),
 ('funds.reverse','冲销收付款及期初调整'),('funds.opening','录入期初与历史来源绑定'),
 ('funds.activate','启用轻量资金'),('funds.adjust','登记往来余额调整');
