-- An explicit original line preserves tenant-scoped inventory provenance. The
-- exact return cost is a server-calculated draft snapshot, never a unit cost.
ALTER TABLE forge.inventory_document_lines
 ADD COLUMN original_line_id uuid,
 ADD CONSTRAINT inventory_line_original_fk
  FOREIGN KEY(organization_id,original_line_id)
  REFERENCES forge.inventory_document_lines(organization_id,id),
 ADD CONSTRAINT inventory_line_original_not_self CHECK(id<>original_line_id);
CREATE INDEX inventory_line_original
 ON forge.inventory_document_lines(organization_id,original_line_id)
 WHERE original_line_id IS NOT NULL;
ALTER TABLE forge.sales_document_lines
 ADD COLUMN return_cost numeric(20,4)
 CHECK(return_cost>=0 AND return_cost<1e16);

-- Invoker rights retain FORCE RLS. This predicate checks frozen provenance;
-- current return quotas and final monetary tails are checked at POST under locks.
CREATE FUNCTION forge.sales_return_source_valid(org uuid, return_line uuid)
 RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS (
  SELECT 1 FROM forge.inventory_document_lines l
  JOIN forge.inventory_documents d
   ON (d.organization_id,d.id)=(l.organization_id,l.document_id)
  JOIN forge.sales_documents sd
   ON (sd.organization_id,sd.id)=(d.organization_id,d.id)
  JOIN forge.sales_document_lines sl
   ON (sl.organization_id,sl.document_id,sl.id)=(l.organization_id,l.document_id,l.id)
  JOIN forge.sales_orders o
   ON (o.organization_id,o.id)=(sl.organization_id,sl.order_id)
  JOIN forge.inventory_document_lines original
   ON (original.organization_id,original.id)=(l.organization_id,l.original_line_id)
  JOIN forge.inventory_documents od
   ON (od.organization_id,od.id)=(original.organization_id,original.document_id)
  JOIN forge.sales_documents osd
   ON (osd.organization_id,osd.id)=(od.organization_id,od.id)
  JOIN forge.sales_document_lines osl
   ON (osl.organization_id,osl.document_id,osl.id)=
      (original.organization_id,original.document_id,original.id)
  JOIN forge.inventory_movements issue
   ON (issue.organization_id,issue.document_id,issue.line_id,issue.operation_id)=
      (original.organization_id,original.document_id,original.id,original.document_id)
   AND issue.kind='ISSUE'
  WHERE l.organization_id=org AND l.id=return_line
   AND d.type='SALES_RETURN' AND sd.kind='RETURN'
   AND od.type='SALES_SHIPMENT' AND od.status='POSTED' AND osd.kind='SHIPMENT'
   AND sd.original_document_id=od.id AND sl.shipment_line_id=original.id
   AND sd.order_id=o.id AND osd.order_id=o.id AND osl.order_id=o.id
   AND sl.order_line_id=osl.order_line_id
   AND d.warehouse_id=od.warehouse_id AND d.warehouse_id=o.warehouse_id
   AND l.product_id=original.product_id AND l.unit_id=original.unit_id
   AND l.unit_to_base_factor=original.unit_to_base_factor
   AND l.conversion_version=original.conversion_version
   AND l.product_label=original.product_label AND l.unit_label=original.unit_label
   AND l.direction='IN' AND original.direction='OUT' AND l.qty>0
   AND l.input_unit_cost IS NULL AND l.reservation_source_line_id IS NULL
   AND sl.unit_price=osl.unit_price AND sl.return_cost IS NOT NULL
   AND original.original_line_id IS NULL AND osl.shipment_line_id IS NULL
   AND forge.sales_shipment_source_valid(org,original.id)
   AND issue.base_qty=-original.base_qty AND issue.value_delta<=0
   AND issue.warehouse_id=od.warehouse_id AND issue.product_id=original.product_id
 );
$$;
REVOKE ALL ON FUNCTION forge.sales_return_source_valid(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION forge.sales_return_source_valid(uuid,uuid) TO forge_app;

CREATE FUNCTION forge.sales_return_source_guard() RETURNS trigger LANGUAGE plpgsql AS $$
 DECLARE rec record; stock record; sales record;
 BEGIN
  IF TG_OP='DELETE' THEN rec:=OLD; ELSE rec:=NEW; END IF;
  SELECT l.original_line_id,d.type INTO stock FROM forge.inventory_document_lines l
   JOIN forge.inventory_documents d
    ON (d.organization_id,d.id)=(l.organization_id,l.document_id)
   WHERE l.organization_id=rec.organization_id AND l.id=rec.id;
  -- Replaced draft rows may have been removed by the time deferred guards run.
  IF NOT FOUND THEN RETURN rec; END IF;
  SELECT shipment_line_id,return_cost INTO sales FROM forge.sales_document_lines
   WHERE organization_id=rec.organization_id AND id=rec.id;
  IF stock.type='SALES_RETURN' THEN
   IF NOT forge.sales_return_source_valid(rec.organization_id,rec.id) THEN
    RAISE EXCEPTION 'Sales return source does not match the original posted shipment'
     USING ERRCODE='23514';
   END IF;
  ELSIF stock.original_line_id IS NOT NULL OR sales.return_cost IS NOT NULL THEN
   RAISE EXCEPTION 'Sales return source and cost require a sales return document'
    USING ERRCODE='23514';
  END IF;
  RETURN rec;
 END;
$$;
CREATE CONSTRAINT TRIGGER sales_return_inventory_source_guard
 AFTER INSERT OR UPDATE ON forge.inventory_document_lines
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
 EXECUTE FUNCTION forge.sales_return_source_guard();
CREATE CONSTRAINT TRIGGER sales_return_line_source_guard
 AFTER INSERT OR DELETE ON forge.sales_document_lines
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
 EXECUTE FUNCTION forge.sales_return_source_guard();
REVOKE ALL ON FUNCTION forge.sales_return_source_guard() FROM PUBLIC;
