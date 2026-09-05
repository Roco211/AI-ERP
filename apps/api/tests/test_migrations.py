"""Exercise empty database and v0.4 upgrade paths on disposable, isolated databases."""

import os
import subprocess
import sys
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
                        ),
                    )
                if revision in {"0008_purchasing", "0009_sales"}:
                    with target.begin() as db:
                        seed_purchase_upgrade_fixture(db)
                if revision == "0009_sales":
                    with target.begin() as db:
                        seed_sales_upgrade_fixture(db)
        with target.connect() as db:
            assert (
                db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0010_sales_shipments"
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
            ):
                assert (
                    db.execute(
                        text("SELECT on_hand_qty FROM forge.inventory_balances")
                    ).scalar_one()
                    == 4
                )
                assert (
                    db.execute(
                        text(
                            "SELECT value_delta FROM forge.inventory_movements WHERE kind='RECEIVE'"
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
            if baseline in {"0008_purchasing", "0009_sales"}:
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
            if baseline == "0009_sales":
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
                assert (
                    db.execute(
                        text("SELECT remaining_qty FROM forge.inventory_reservations")
                    ).scalar_one()
                    == 2
                )
                assert (
                    db.execute(
                        text(
                            "SELECT count(*) FROM forge.inventory_document_lines WHERE "
                            "reservation_source_line_id IS NOT NULL"
                        )
                    ).scalar_one()
                    == 0
                )
            for table in (
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
