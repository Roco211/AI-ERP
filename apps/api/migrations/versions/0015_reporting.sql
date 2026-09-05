-- Read models reuse existing facts and their FORCE RLS; no second fact store.
INSERT INTO forge.permissions(code,description) VALUES ('dashboard.read','查看经营概览入口');
CREATE INDEX reporting_posted_documents
 ON forge.inventory_documents(organization_id,posted_at,id)
 WHERE status='POSTED' AND type IN ('SALES_SHIPMENT','SALES_RETURN');
CREATE INDEX reporting_cash_dates
 ON forge.funds_cash_documents(organization_id,side,business_date,id);
