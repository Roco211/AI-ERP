-- Shipment lines keep their own immutable identity and explicitly reference the
-- distinct inventory line that established the original order reservation.
ALTER TABLE forge.inventory_document_lines
 ADD COLUMN reservation_source_line_id uuid,
 ADD CONSTRAINT inventory_line_reservation_source_fk
  FOREIGN KEY(organization_id,reservation_source_line_id)
  REFERENCES forge.inventory_document_lines(organization_id,id),
 ADD CONSTRAINT inventory_line_reservation_source_not_self
  CHECK(id<>reservation_source_line_id);
CREATE INDEX inventory_line_reservation_source
 ON forge.inventory_document_lines(organization_id,reservation_source_line_id)
 WHERE reservation_source_line_id IS NOT NULL;

-- Invoker rights preserve FORCE RLS. This read-only predicate is shared by the
-- commit-time SQL guard and InventoryEngine's runtime capability check. Null
-- historical pricing fixtures stay readable but cannot consume any reservation.
CREATE FUNCTION forge.sales_shipment_source_valid(org uuid, shipment_line uuid)
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
  JOIN forge.sales_order_lines ol
   ON (ol.organization_id,ol.order_id,ol.id)=(sl.organization_id,sl.order_id,sl.order_line_id)
  JOIN forge.inventory_document_lines r
   ON (r.organization_id,r.id)=(l.organization_id,l.reservation_source_line_id)
  JOIN forge.inventory_documents rd
   ON (rd.organization_id,rd.id)=(r.organization_id,r.document_id)
  JOIN forge.sales_documents rsd
   ON (rsd.organization_id,rsd.id)=(rd.organization_id,rd.id)
  JOIN forge.sales_document_lines rsl
   ON (rsl.organization_id,rsl.document_id,rsl.id)=(r.organization_id,r.document_id,r.id)
  WHERE l.organization_id=org AND l.id=shipment_line
   AND d.type='SALES_SHIPMENT' AND sd.kind='SHIPMENT'
   AND rd.type='SALES_RESERVATION' AND rd.status='POSTED' AND rsd.kind='RESERVATION'
   AND r.reservation_source_line_id IS NULL
   AND sd.order_id=o.id AND rsd.order_id=o.id AND rsl.order_id=o.id
   AND rsl.order_line_id=ol.id
   AND d.warehouse_id=o.warehouse_id AND rd.warehouse_id=o.warehouse_id
   AND l.product_id=ol.product_id AND r.product_id=ol.product_id
   AND l.unit_id=ol.unit_id AND r.unit_id=ol.unit_id
   AND l.unit_to_base_factor=ol.unit_to_base_factor
   AND r.unit_to_base_factor=ol.unit_to_base_factor
   AND l.conversion_version=ol.conversion_version AND r.conversion_version=ol.conversion_version
   AND l.product_label=ol.product_label AND r.product_label=ol.product_label
   AND l.unit_label=ol.unit_label AND r.unit_label=ol.unit_label
   AND l.direction='OUT' AND r.direction='OUT' AND l.input_unit_cost IS NULL
   AND l.qty>0 AND r.qty=ol.qty AND r.base_qty=ol.base_qty
   AND sl.unit_price=ol.unit_price AND rsl.unit_price=ol.unit_price
   AND sl.amount=round(l.qty*ol.unit_price,4) AND rsl.amount=ol.amount
   AND sl.shipment_line_id IS NULL AND rsl.shipment_line_id IS NULL
 );
$$;
REVOKE ALL ON FUNCTION forge.sales_shipment_source_valid(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION forge.sales_shipment_source_valid(uuid,uuid) TO forge_app;

CREATE FUNCTION forge.sales_shipment_source_guard() RETURNS trigger LANGUAGE plpgsql AS $$
 DECLARE source_id uuid; rec record;
 BEGIN
  IF TG_OP='DELETE' THEN rec:=OLD; ELSE rec:=NEW; END IF;
  SELECT reservation_source_line_id INTO source_id FROM forge.inventory_document_lines
   WHERE organization_id=rec.organization_id AND id=rec.id;
  -- A replaced draft line may already have been deleted when deferred checks run.
  IF source_id IS NULL THEN RETURN rec; END IF;
  IF NOT forge.sales_shipment_source_valid(rec.organization_id,rec.id) THEN
   RAISE EXCEPTION 'Shipment reservation source does not match the frozen sales order'
    USING ERRCODE='23514';
  END IF;
  RETURN rec;
 END;
$$;
-- Commands insert inventory lines before their sales association. Check the
-- completed transaction; InventoryEngine separately checks immediately before ISSUE.
CREATE CONSTRAINT TRIGGER sales_shipment_inventory_source_guard
 AFTER INSERT OR UPDATE ON forge.inventory_document_lines
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
 EXECUTE FUNCTION forge.sales_shipment_source_guard();
CREATE CONSTRAINT TRIGGER sales_shipment_line_source_guard
 AFTER INSERT OR DELETE ON forge.sales_document_lines
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
 EXECUTE FUNCTION forge.sales_shipment_source_guard();
REVOKE ALL ON FUNCTION forge.sales_shipment_source_guard() FROM PUBLIC;
