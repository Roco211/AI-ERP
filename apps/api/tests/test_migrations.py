"""Exercise empty database and v0.4 upgrade paths on disposable, isolated databases."""

import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from forge_erp.core.config import settings


@pytest.mark.parametrize(
    "baseline",
    [
        None,
        "0001_bootstrap",
        "0004_product_embeddings",
        "0005_inventory",
        "0006_inventory_snapshot",
        "0007_inventory_projection",
        "0008_purchasing",
        "0009_sales",
        "0010_sales_shipments",
        "0011_sales_returns",
        "0015_reporting",
    ],
)
def test_clean_and_bootstrap_migrations(baseline):
    cfg = settings()
    name = "forge_migration_test_" + uuid4().hex
    root = Path(__file__).resolve().parents[3]
    admin = create_engine(cfg.migration_database_url, isolation_level="AUTOCOMMIT")
    url = make_url(cfg.migration_database_url).set(database=name)
    target = create_engine(url)
    try:
        with admin.connect() as db:
            db.execute(text(f'CREATE DATABASE "{name}"'))
        # Same prerequisites as infra/postgres init: role is cluster-wide, extensions per DB.
        with target.begin() as db:
            db.execute(text("CREATE EXTENSION vector"))
            db.execute(text("CREATE EXTENSION pg_trgm"))
        env = dict(os.environ, MIGRATION_DATABASE_URL=url.render_as_string(hide_password=False))
        for revision in [baseline, "head"] if baseline else ["head"]:
            run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "alembic",
                    "-c",
                    "apps/api/alembic.ini",
                    "upgrade",
                    revision,
                ],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert run.returncode == 0, "Migration failed in disposable database"
            if revision == baseline and baseline in (
                "0004_product_embeddings",
                "0005_inventory",
                "0006_inventory_snapshot",
                "0007_inventory_projection",
                "0008_purchasing",
                "0009_sales",
                "0010_sales_shipments",
                "0011_sales_returns",
            ):
                with target.begin() as db:
                    seed_upgrade_fixture(
                        db,
                        with_stock=baseline
                        in (
                            "0005_inventory",
                            "0006_inventory_snapshot",
                            "0007_inventory_projection",
                            "0008_purchasing",
                            "0009_sales",
                            "0010_sales_shipments",
                            "0011_sales_returns",
                        ),
                    )
                if revision in {
                    "0008_purchasing",
                    "0009_sales",
                    "0010_sales_shipments",
                    "0011_sales_returns",
                }:
                    with target.begin() as db:
                        seed_purchase_upgrade_fixture(db)
                if revision in {"0009_sales", "0010_sales_shipments", "0011_sales_returns"}:
                    with target.begin() as db:
                        seed_sales_upgrade_fixture(db)
                if revision in {"0010_sales_shipments", "0011_sales_returns"}:
                    with target.begin() as db:
                        seed_shipment_upgrade_fixture(db)
                if revision == "0011_sales_returns":
                    with target.begin() as db:
                        seed_return_upgrade_fixture(db)
        with target.connect() as db:
            assert (
                db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0018_ai_retention"
            )
            assert db.execute(text("SELECT count(*) FROM forge.products")).scalar_one() == (
                1
                if baseline
                in (
                    "0004_product_embeddings",
                    "0005_inventory",
                    "0006_inventory_snapshot",
                    "0007_inventory_projection",
                    "0008_purchasing",
                    "0009_sales",
                    "0010_sales_shipments",
                    "0011_sales_returns",
                )
                else 0
            )
            if baseline in (
                "0004_product_embeddings",
                "0005_inventory",
                "0006_inventory_snapshot",
                "0007_inventory_projection",
                "0008_purchasing",
                "0009_sales",
                "0010_sales_shipments",
                "0011_sales_returns",
            ):
                assert db.execute(text("SELECT price FROM forge.product_prices")).scalar_one() == 2
                assert (
                    db.execute(
                        text("SELECT unit_to_base_factor FROM forge.product_units")
                    ).scalar_one()
                    == 1
                )
            if baseline in (
                "0005_inventory",
                "0006_inventory_snapshot",
                "0007_inventory_projection",
                "0008_purchasing",
                "0009_sales",
                "0010_sales_shipments",
                "0011_sales_returns",
            ):
                assert db.execute(
                    text("SELECT on_hand_qty FROM forge.inventory_balances")
                ).scalar_one() == (
                    Decimal("3.4")
                    if baseline == "0011_sales_returns"
                    else 3
                    if baseline == "0010_sales_shipments"
                    else 4
                )
                assert (
                    db.execute(
                        text(
                            "SELECT value_delta FROM forge.inventory_movements "
                            "WHERE kind='RECEIVE' AND sequence=1"
                        )
                    ).scalar_one()
                    == 5
                )
                assert (
                    db.execute(
                        text("SELECT status FROM forge.inventory_documents WHERE type='OPENING'")
                    ).scalar_one()
                    == "POSTED"
                )
            if baseline in {
                "0008_purchasing",
                "0009_sales",
                "0010_sales_shipments",
                "0011_sales_returns",
            }:
                assert (
                    db.execute(text("SELECT status FROM forge.purchase_orders")).scalar_one()
                    == "CONFIRMED"
                )
                assert (
                    db.execute(text("SELECT amount FROM forge.purchase_order_lines")).scalar_one()
                    == 14
                )
                assert (
                    db.execute(text("SELECT count(*) FROM forge.role_permissions")).scalar_one()
                    == 1
                )
            if baseline in {"0009_sales", "0010_sales_shipments", "0011_sales_returns"}:
                assert (
                    db.execute(text("SELECT status FROM forge.sales_orders")).scalar_one()
                    == "CONFIRMED"
                )
                assert (
                    db.execute(
                        text("SELECT price_source->>'source' FROM forge.sales_order_lines")
                    ).scalar_one()
                    == "manual"
                )
                assert db.execute(
                    text("SELECT remaining_qty FROM forge.inventory_reservations")
                ).scalar_one() == (
                    1 if baseline in {"0010_sales_shipments", "0011_sales_returns"} else 2
                )
                assert db.execute(
                    text(
                        "SELECT count(*) FROM forge.inventory_document_lines WHERE "
                        "reservation_source_line_id IS NOT NULL"
                    )
                ).scalar_one() == (
                    1 if baseline in {"0010_sales_shipments", "0011_sales_returns"} else 0
                )
            if baseline in {"0010_sales_shipments", "0011_sales_returns"}:
                assert (
                    db.execute(
                        text(
                            "SELECT -value_delta FROM forge.inventory_movements WHERE kind='ISSUE'"
                        )
                    ).scalar_one()
                    == 1.25
                )
                assert db.execute(
                    text(
                        "SELECT count(*) FROM forge.sales_document_lines WHERE return_cost "
                        "IS NOT NULL"
                    )
                ).scalar_one() == (1 if baseline == "0011_sales_returns" else 0)
                assert db.execute(
                    text(
                        "SELECT count(*) FROM forge.inventory_document_lines WHERE "
                        "original_line_id IS NOT NULL"
                    )
                ).scalar_one() == (1 if baseline == "0011_sales_returns" else 0)
            if baseline == "0011_sales_returns":
                assert db.execute(
                    text(
                        "SELECT return_cost FROM forge.sales_document_lines "
                        "WHERE return_cost IS NOT NULL"
                    )
                ).scalar_one() == Decimal("0.5")
                assert db.execute(
                    text("SELECT value_delta FROM forge.inventory_movements WHERE sequence=4")
                ).scalar_one() == Decimal("0.5")
            assert db.execute(text("SELECT count(*) FROM forge.funds_sources")).scalar_one() == 0
            assert db.execute(text("SELECT count(*) FROM forge.funds_activation")).scalar_one() == 0
            for table in (
                "import_batches",
                "import_rows",
                "replenishment_creations",
                "funds_activation",
                "funds_sources",
                "funds_entries",
                "funds_cash_documents",
                "funds_cash_allocations",
                "funds_cash_reversals",
                "funds_operations",
                "sales_orders",
                "sales_order_lines",
                "sales_documents",
                "sales_document_lines",
            ):
                assert db.execute(
                    text(
                        "SELECT relforcerowsecurity FROM pg_class WHERE oid=CAST(:name AS regclass)"
                    ),
                    {"name": "forge." + table},
                ).scalar_one()
            assert db.execute(
                text(
                    "SELECT relforcerowsecurity FROM pg_class WHERE oid='forge.products'::regclass"
                )
            ).scalar_one()
    finally:
        target.dispose()
        with admin.connect() as db:
            db.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def seed_upgrade_fixture(db, with_stock):
    """Historical data in a disposable DB proves upgrades preserve existing facts."""
    org, unit, category, product, warehouse, user, doc, line = [uuid4() for _ in range(8)]
    p = dict(
        org=org,
        unit=unit,
        category=category,
        product=product,
        wh=warehouse,
        user=user,
        doc=doc,
        line=line,
    )
    statements = [
        "INSERT INTO forge.organizations(id,code,name) VALUES (:org,'MIGRATION','Migration')",
        "INSERT INTO forge.units(id,organization_id,code,name) VALUES (:unit,:org,'PCS','个')",
        "INSERT INTO forge.categories(id,organization_id,code,name) VALUES (:category,"
        ":org,'BOLT','螺栓')",
        "INSERT INTO forge.warehouses(id,organization_id,code,name) "
        "VALUES (:wh,:org,'MAIN','主仓')",
        "INSERT INTO forge.products(id,organization_id,sku,name,category_id,"
        "base_unit_id,default_purchase_unit_id,default_sales_unit_id) VALUES "
        "(:product,:org,'BOLT','Bolt',:category,:unit,:unit,:unit)",
        "INSERT INTO forge.product_units(organization_id,product_id,unit_id,"
        "unit_to_base_factor) VALUES (:org,:product,:unit,1)",
        "INSERT INTO forge.product_prices(organization_id,product_id,price_type,"
        "price) VALUES (:org,:product,'standard',2)",
    ]
    if with_stock:
        statements += [
            "INSERT INTO forge.users(id,organization_id,email,display_name,"
            "password_hash) VALUES (:user,:org,'migration@example.test','Fixture',"
            "'not-a-live-password-hash')",
            "INSERT INTO forge.inventory_documents(id,organization_id,number,type,"
            "reason,warehouse_id,created_by) VALUES (:doc,:org,'MIGRATION-OPEN',"
            "'OPENING','Migration fixture',:wh,:user)",
            "INSERT INTO forge.inventory_document_lines(id,organization_id,"
            "document_id,line_no,product_id,unit_id,product_label,unit_label,qty,"
            "unit_to_base_factor,base_qty,conversion_version,direction,"
            "input_unit_cost) VALUES (:line,:org,:doc,1,:product,:unit,'Bolt','个',4,1,"
            "4,1,'IN',1.25)",
            "INSERT INTO forge.inventory_balances(organization_id,warehouse_id,"
            "product_id,on_hand_qty,inventory_value,avg_unit_cost,version) VALUES "
            "(:org,:wh,:product,4,5,1.25,1)",
            "INSERT INTO forge.inventory_movements(organization_id,warehouse_id,"
            "product_id,sequence,kind,base_qty,value_delta,before_avg_cost,"
            "after_avg_cost,document_id,line_id,operation_id,actor_id,request_id) "
            "VALUES (:org,:wh,:product,1,'RECEIVE',4,5,0,1.25,:doc,:line,:doc,:user,"
            "'migration-fixture')",
            "UPDATE forge.inventory_documents SET status='POSTED',posted_at=now(),"
            "version=2 WHERE id=:doc",
        ]
    for statement in statements:
        db.execute(text(statement), p)
    if (
        with_stock
        and db.execute(
            text(
                "SELECT 1 FROM information_schema.columns WHERE "
                "table_schema='forge' AND table_name='inventory_balances' "
                "AND column_name='movement_sequence'"
            )
        ).first()
    ):
        db.execute(
            text(
                "UPDATE forge.inventory_balances SET movement_sequence=1 WHERE organization_id=:org"
            ),
            p,
        )


def seed_purchase_upgrade_fixture(db):
    row = (
        db.execute(
            text(
                "SELECT b.organization_id AS org,b.warehouse_id AS wh,b.product_id AS product,"
                "p.base_unit_id AS unit,u.id AS actor FROM "
                "forge.inventory_balances b JOIN forge.products p "
                "ON p.id=b.product_id JOIN forge.users u ON u.organization_id=b.organization_id"
            )
        )
        .mappings()
        .one()
    )
    params = dict(row) | {"supplier": uuid4(), "order": uuid4(), "role": uuid4()}
    statements = [
        "INSERT INTO forge.suppliers(id,organization_id,code,name) "
        "VALUES (:supplier,:org,'OLD','Old supplier')",
        "INSERT INTO forge.roles(id,organization_id,code,name) VALUES "
        "(:role,:org,'OLD','Old role')",
        "INSERT INTO forge.role_permissions VALUES (:org,:role,'purchase.order.confirm')",
        "INSERT INTO forge.purchase_orders(id,organization_id,number,supplier_id,supplier_name,"
        "warehouse_id,warehouse_name,reason,amount,created_by) VALUES "
        "(:order,:org,'OLD-PO',:supplier,'Old supplier',:wh,'主仓','历史采购',14,:actor)",
        "INSERT INTO "
        "forge.purchase_order_lines(organization_id,order_id,line_no,product_id,unit_id,"
        "product_label,unit_label,qty,unit_to_base_factor,base_qty,"
        "conversion_version,unit_price,amount) "
        "VALUES (:org,:order,1,:product,:unit,'Bolt','个',7,1,7,1,2,14)",
        "UPDATE forge.purchase_orders SET "
        "status='CONFIRMED',confirmed_at=now(),version=2 WHERE id=:order",
    ]
    for sql in statements:
        db.execute(text(sql), params)


def seed_sales_upgrade_fixture(db):
    """Preserve a real S1 confirmed order, reservation source and ledger through S2 migration."""
    row = (
        db.execute(
            text(
                "SELECT b.organization_id AS org,b.warehouse_id AS wh,"
                "b.product_id AS product,p.base_unit_id AS unit,u.id AS actor "
                "FROM forge.inventory_balances b "
                "JOIN forge.products p ON p.id=b.product_id "
                "JOIN forge.users u ON u.organization_id=b.organization_id"
            )
        )
        .mappings()
        .one()
    )
    params = dict(row) | {
        "customer": uuid4(),
        "order": uuid4(),
        "line": uuid4(),
        "doc": uuid4(),
        "source": uuid4(),
        "reservation": uuid4(),
    }
    statements = [
        "INSERT INTO forge.customers(id,organization_id,code,name) "
        "VALUES(:customer,:org,'OLD-C','Sales customer')",
        "INSERT INTO "
        "forge.sales_orders(id,organization_id,number,customer_id,"
        "customer_name,warehouse_id,warehouse_name,reason,amount,"
        "created_by) VALUES(:order,:org,'OLD-SO',:customer,'Sales "
        "customer',:wh,'主仓','Sales fixture',4,:actor)",
        "INSERT INTO "
        "forge.sales_order_lines(id,organization_id,order_id,line_no,"
        "product_id,unit_id,product_label,unit_label,qty,"
        "unit_to_base_factor,base_qty,conversion_version,pricing_mode,"
        "price_source,unit_price,amount) "
        "VALUES(:line,:org,:order,1,:product,:unit,'Bolt','个',2,1,2,1,"
        "'MANUAL','{\"source\":\"manual\"}',2,4)",
        "INSERT INTO "
        "forge.inventory_documents(id,organization_id,number,type,reason,"
        "warehouse_id,created_by) "
        "VALUES(:doc,:org,'OLD-SR','SALES_RESERVATION','Sales "
        "fixture',:wh,:actor)",
        "INSERT INTO "
        "forge.sales_documents(id,organization_id,order_id,kind) "
        "VALUES(:doc,:org,:order,'RESERVATION')",
        "INSERT INTO "
        "forge.inventory_document_lines(id,organization_id,document_id,"
        "line_no,product_id,unit_id,product_label,unit_label,qty,"
        "unit_to_base_factor,base_qty,conversion_version,direction) "
        "VALUES(:source,:org,:doc,1,:product,:unit,'Bolt','个',2,1,2,1,"
        "'OUT')",
        "INSERT INTO "
        "forge.sales_document_lines(id,organization_id,document_id,"
        "order_id,order_line_id,unit_price,amount) "
        "VALUES(:source,:org,:doc,:order,:line,2,4)",
        "INSERT INTO "
        "forge.inventory_reservations(id,organization_id,warehouse_id,"
        "product_id,document_id,line_id,reserved_qty,remaining_qty,"
        "version) "
        "VALUES(:reservation,:org,:wh,:product,:doc,:source,2,2,1)",
        "INSERT INTO "
        "forge.inventory_movements(organization_id,warehouse_id,"
        "product_id,sequence,kind,base_qty,reserved_qty_delta,"
        "value_delta,before_avg_cost,after_avg_cost,document_id,line_id,"
        "operation_id,reservation_id,reservation_reserved_delta,actor_id,"
        "request_id) "
        "VALUES(:org,:wh,:product,2,'RESERVE',0,2,0,1.25,1.25,:doc,"
        ":source,:doc,:reservation,2,:actor,'sales-migration-fixture')",
        "UPDATE forge.inventory_balances SET "
        "reserved_qty=2,version=version+1,movement_sequence=2 WHERE "
        "organization_id=:org",
        "UPDATE forge.inventory_documents SET "
        "status='POSTED',posted_at=now(),version=2 WHERE id=:doc",
        "UPDATE forge.sales_orders SET "
        "status='CONFIRMED',confirmed_at=now(),version=2 WHERE id=:order",
    ]
    for sql in statements:
        db.execute(text(sql), params)


def seed_shipment_upgrade_fixture(db):
    """Preserve a posted S2 shipment, its separate source and consumed reservation."""
    row = (
        db.execute(
            text("""SELECT sd.organization_id AS org,sd.order_id AS order,
        sl.order_line_id AS orderline,l.id AS source,d.warehouse_id AS wh,
        l.product_id AS product,l.unit_id AS unit,d.created_by AS actor,r.id AS reservation
        FROM forge.sales_documents sd JOIN forge.inventory_documents d ON d.id=sd.id
        JOIN forge.sales_document_lines sl ON sl.document_id=sd.id
        JOIN forge.inventory_document_lines l ON l.id=sl.id
        JOIN forge.inventory_reservations r ON r.line_id=l.id
        WHERE sd.kind='RESERVATION'""")
        )
        .mappings()
        .one()
    )
    params = dict(row) | {"doc": uuid4(), "line": uuid4()}
    statements = [
        "INSERT INTO forge.inventory_documents(id,organization_id,number,type,reason,"
        "warehouse_id,created_by) VALUES(:doc,:org,'OLD-SS','SALES_SHIPMENT','Shipment fixture',"
        ":wh,:actor)",
        "INSERT INTO forge.sales_documents(id,organization_id,order_id,kind) VALUES(:doc,:org,"
        ":order,'SHIPMENT')",
        "INSERT INTO forge.inventory_document_lines(id,organization_id,document_id,line_no,"
        "product_id,unit_id,product_label,unit_label,qty,unit_to_base_factor,base_qty,"
        "conversion_version,direction,reservation_source_line_id) VALUES(:line,:org,:doc,1,"
        ":product,:unit,'Bolt','个',1,1,1,1,'OUT',:source)",
        "INSERT INTO forge.sales_document_lines(id,organization_id,document_id,order_id,"
        "order_line_id,unit_price,amount) VALUES(:line,:org,:doc,:order,:orderline,2,2)",
        "INSERT INTO forge.inventory_movements(organization_id,warehouse_id,product_id,sequence,"
        "kind,base_qty,reserved_qty_delta,value_delta,before_avg_cost,after_avg_cost,"
        "document_id,line_id,operation_id,reservation_id,reservation_consumed_delta,actor_id,"
        "request_id) VALUES(:org,:wh,:product,3,'ISSUE',-1,-1,-1.25,1.25,1.25,:doc,:line,:doc,"
        ":reservation,1,:actor,'sales-shipment-migration-fixture')",
        "UPDATE forge.inventory_reservations SET consumed_qty=1,remaining_qty=1,"
        "version=version+1 WHERE id=:reservation",
        "UPDATE forge.inventory_balances SET on_hand_qty=3,reserved_qty=1,inventory_value=3.75,"
        "version=version+1,movement_sequence=3 WHERE organization_id=:org",
        "UPDATE forge.inventory_documents SET status='POSTED',posted_at=now(),version=2 WHERE "
        "id=:doc",
    ]
    for sql in statements:
        db.execute(text(sql), params)


def seed_return_upgrade_fixture(db):
    """An actual v0.8 return survives funds migration without guessed opening cash/debt."""
    row = db.execute(
        text(
            "SELECT d.organization_id org,sd.order_id AS order_id,"
            "sl.order_line_id orderline,d.id original,l.id original_line,d.warehouse_id wh,"
            "l.product_id product,l.unit_id unit,d.created_by actor "
            "FROM forge.sales_documents sd JOIN forge.inventory_documents d ON d.id=sd.id "
            "JOIN forge.sales_document_lines sl ON sl.document_id=d.id "
            "JOIN forge.inventory_document_lines l ON l.id=sl.id WHERE sd.kind='SHIPMENT'"
        )
    )
    params = dict(row.mappings().one()) | {"doc": uuid4(), "line": uuid4()}
    statements = [
        "INSERT INTO forge.inventory_documents(id,organization_id,number,type,reason,warehouse_id,"
        "created_by) VALUES(:doc,:org,'OLD-SRET','SALES_RETURN','Return fixture',:wh,:actor)",
        "INSERT INTO forge.sales_documents(id,organization_id,order_id,kind,original_document_id) "
        "VALUES(:doc,:org,:order_id,'RETURN',:original)",
        "INSERT INTO forge.inventory_document_lines(id,organization_id,document_id,"
        "line_no,product_id,"
        "unit_id,product_label,unit_label,qty,unit_to_base_factor,base_qty,conversion_version,direction,"
        "original_line_id) VALUES(:line,:org,:doc,1,:product,:unit,'Bolt','个',"
        "0.4,1,0.4,1,'IN',:original_line)",
        "INSERT INTO forge.sales_document_lines(id,organization_id,document_id,"
        "order_id,order_line_id,"
        "shipment_line_id,unit_price,amount,return_cost) "
        "VALUES(:line,:org,:doc,:order_id,:orderline,:original_line,2,0.8,0.5)",
        "INSERT INTO forge.inventory_movements(organization_id,warehouse_id,product_id,"
        "sequence,kind,"
        "base_qty,value_delta,before_avg_cost,after_avg_cost,document_id,line_id,operation_id,actor_id,"
        "request_id) VALUES(:org,:wh,:product,4,'RECEIVE',0.4,0.5,1.25,1.25,:doc,:line,:doc,:actor,"
        "'sales-return-migration-fixture')",
        "UPDATE forge.inventory_balances SET on_hand_qty=3.4,inventory_value=4.25,"
        "version=version+1,movement_sequence=4 WHERE organization_id=:org",
        "UPDATE forge.inventory_documents SET status='POSTED',posted_at=now(),"
        "version=2 WHERE id=:doc",
    ]
    for statement in statements:
        db.execute(text(statement), params)


@pytest.mark.parametrize("baseline", ["0012_funds", "0015_reporting"])
def test_operations_upgrade_preserves_every_funds_and_commercial_fact(baseline):
    """Populated released databases preserve all original facts through the current head."""
    cfg = settings()
    name = "forge_migration_test_" + uuid4().hex
    root = Path(__file__).resolve().parents[3]
    admin = create_engine(cfg.migration_database_url, isolation_level="AUTOCOMMIT")
    url = make_url(cfg.migration_database_url).set(database=name)
    target = create_engine(url)

    def migrate(revision):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "apps/api/alembic.ini", "upgrade", revision],
            cwd=root,
            env=dict(os.environ, MIGRATION_DATABASE_URL=url.render_as_string(hide_password=False)),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, "Operations migration failed in disposable database"

    def fingerprints(db, tables):
        return {
            table: db.execute(
                text(
                    "SELECT count(*),md5(string_agg(to_jsonb(t)::text,'' "
                    "ORDER BY to_jsonb(t)::text)) "
                    f"FROM forge.{table} t"
                )
            ).one()
            for table in tables
        }

    try:
        with admin.connect() as db:
            db.execute(text(f'CREATE DATABASE "{name}"'))
        with target.begin() as db:
            db.execute(text("CREATE EXTENSION vector"))
            db.execute(text("CREATE EXTENSION pg_trgm"))
        migrate(baseline)
        with target.begin() as db:
            seed_upgrade_fixture(db, with_stock=True)
            seed_purchase_upgrade_fixture(db)
            seed_sales_upgrade_fixture(db)
            seed_shipment_upgrade_fixture(db)
            seed_return_upgrade_fixture(db)
            p = dict(
                db.execute(
                    text("""
                SELECT o.organization_id org,o.created_by actor,o.customer_id customer,
                d.id shipment,r.id returned FROM forge.sales_orders o
                JOIN forge.sales_documents d ON d.order_id=o.id AND d.kind='SHIPMENT'
                JOIN forge.sales_documents r ON r.original_document_id=d.id AND r.kind='RETURN'
            """)
                )
                .mappings()
                .one()
            ) | {"source": uuid4(), "cash": uuid4()}
            for sql in (
                "INSERT INTO forge.funds_activation(organization_id,business_date,reason,"
                "created_by) "
                "VALUES(:org,current_date,'Verified historical cutover',:actor)",
                "INSERT INTO forge.funds_sources(id,organization_id,number,side,party_id,"
                "customer_id,"
                "party_name,kind,source_document_id,source_document_number,commercial_amount,"
                "business_date,reason,created_by) VALUES(:source,:org,'OLD-AR','AR',:customer,"
                ":customer,'Sales customer','SHIPMENT',:shipment,'OLD-SS',2,current_date,"
                "'Actual commercial source',:actor)",
                "INSERT INTO forge.funds_entries(organization_id,source_id,kind,amount,"
                "document_id,reason,created_by) VALUES(:org,:source,'ORIGIN',2,:shipment,"
                "'Actual posted amount',:actor),(:org,:source,'RETURN',-0.8,:returned,"
                "'Frozen commercial return',:actor)",
                "INSERT INTO forge.funds_cash_documents(id,organization_id,number,side,party_id,"
                "customer_id,party_name,kind,amount,business_date,method,external_reference,"
                "reason,created_by) VALUES(:cash,:org,'OLD-CASH','AR',:customer,:customer,"
                "'Sales customer','SETTLEMENT',0.5,current_date,'CASH','',"
                "'Historical receipt',:actor)",
                "INSERT INTO forge.funds_cash_allocations(organization_id,cash_id,source_id,side,"
                "party_id,amount) VALUES(:org,:cash,:source,'AR',:customer,0.5)",
                "INSERT INTO forge.funds_cash_reversals(organization_id,cash_id,reason,created_by) "
                "VALUES(:org,:cash,'Historical correction retained',:actor)",
                "INSERT INTO forge.funds_operations(organization_id,actor_id,operation,key,"
                "request_hash,response) VALUES(:org,:actor,'funds.cash.post','old-intent',"
                "'old-hash',jsonb_build_object('id',CAST(:cash AS text),'status','POSTED'))",
            ):
                db.execute(text(sql), p)
            tables = (
                db.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='forge' "
                        "AND tablename<>'permissions' ORDER BY tablename"
                    )
                )
                .scalars()
                .all()
            )
            before = fingerprints(db, tables)
            assert all(
                before[t][0] > 0
                for t in (
                    "funds_activation",
                    "funds_sources",
                    "funds_entries",
                    "funds_cash_documents",
                    "funds_cash_allocations",
                    "funds_cash_reversals",
                    "funds_operations",
                    "inventory_movements",
                    "inventory_balances",
                    "sales_document_lines",
                )
            )
        migrate("head")
        with target.connect() as db:
            assert (
                db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0018_ai_retention"
            )
            assert fingerprints(db, tables) == before
            for table in ("import_batches", "import_rows", "replenishment_creations"):
                assert db.execute(text(f"SELECT count(*) FROM forge.{table}")).scalar_one() == 0
                assert db.execute(
                    text(
                        "SELECT relrowsecurity AND relforcerowsecurity "
                        "FROM pg_class WHERE oid=CAST(:name AS regclass)"
                    ),
                    {"name": "forge." + table},
                ).scalar_one()
    finally:
        target.dispose()
        with admin.connect() as db:
            db.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
