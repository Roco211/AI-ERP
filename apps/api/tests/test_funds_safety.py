"""Independent funds authorization, permanent replay, concurrency and rollback checks."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal as D
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from test_catalog import catalog_client as catalog_client
from test_catalog import create
from test_purchasing import action as purchase_action
from test_purchasing import po, receive
from test_purchasing import return_draft as purchase_return
from test_sales_orders import sale as sale
from test_sales_returns import issued, return_draft, reverse
from test_sales_shipments import post_shipment, shipment

from forge_erp.core.config import settings
from forge_erp.core.db import sessions, set_tenant
from forge_erp.main import app
from forge_erp.modules.funds.application import shared as funds_shared

TABLES = (
    "funds_activation",
    "funds_sources",
    "funds_entries",
    "funds_cash_documents",
    "funds_cash_allocations",
    "funds_cash_reversals",
    "funds_operations",
)


async def enable(c):
    response = await create(
        c,
        "funds/activate",
        {"business_date": datetime.now(UTC).date().isoformat(), "reason": "测试切点"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def source(c, party, side="AR", amount="10", key=None):
    response = await create(
        c,
        "funds/openings",
        {
            "side": side,
            "party_id": party,
            "amount": amount,
            "refund_balance_confirmed": D(amount) < 0,
            "reason": "明确期初余额",
        },
        key,
    )
    assert response.status_code == 201, response.text
    return response.json()


def cash_body(party, rows, side="AR", kind="SETTLEMENT"):
    return {
        "side": side,
        "party_id": party,
        "kind": kind,
        "business_date": datetime.now(UTC).date().isoformat(),
        "method": "BANK_TRANSFER",
        "external_reference": "funds-safety-only",
        "reason": "实际收付安全测试",
        "allocations": [{"source_id": row["id"], "amount": amount} for row, amount in rows],
    }


async def cash(c, body, key=None):
    return await create(c, "funds/cash", body, key)


async def reverse_cash(c, row, key=None):
    return await create(c, "funds/cash/" + row["id"] + "/reverse", {"reason": "纠正记录"}, key)


async def source_detail(c, row):
    response = await c.get("/api/v1/funds/sources/" + row["id"])
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
async def funds_case(catalog_client, identities):
    c = catalog_client
    await enable(c)
    customer = await create(c, "customers", {"code": "FUNDS-AR", "name": "应收安全客户"})
    supplier = await create(c, "suppliers", {"code": "FUNDS-AP", "name": "应付安全供应商"})
    assert customer.status_code == supplier.status_code == 201
    return {
        "c": c,
        "AR": customer.json()["id"],
        "AP": supplier.json()["id"],
        "identity": identities[0],
    }


async def facts(identity):
    result = {}
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        for table in (*TABLES, "audit_events", "outbox_events"):
            extra = ""
            if table == "audit_events":
                extra = " AND action LIKE 'funds.%'"
            elif table == "outbox_events":
                extra = " AND event_type LIKE 'funds.%'"
            rows = (
                (
                    await db.execute(
                        text(
                            f"SELECT * FROM forge.{table} "
                            f"WHERE organization_id=:org{extra} ORDER BY id"
                        ),
                        identity,
                    )
                )
                .mappings()
                .all()
            )
            result[table] = [committed_fact(table, row) for row in rows]
    return result


def committed_fact(table, row):
    # The running worker can independently advance delivery while a command is
    # being rejected. Atomicity compares the committed event identity/payload,
    # not unrelated attempts or processing timestamps.
    return {
        key: value
        for key, value in dict(row).items()
        if table != "outbox_events" or key not in {"processed_at", "attempts"}
    }


async def expire(identity, key, mode):
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        expired = await db.execute(
            text(
                "UPDATE forge.idempotency_keys SET expires_at=now()-interval '1 second' "
                "WHERE organization_id=:org AND actor_id=:user AND key=:key RETURNING key"
            ),
            identity | {"key": key},
        )
        assert expired.scalar_one() == key
        if mode == "cleaned":
            deleted = await db.execute(
                text(
                    "DELETE FROM forge.idempotency_keys WHERE organization_id=:org "
                    "AND actor_id=:user AND key=:key AND expires_at<=now() RETURNING key"
                ),
                identity | {"key": key},
            )
            assert deleted.scalar_one() == key


def revoke(identity, permission):
    admin = create_engine(settings().migration_database_url)
    try:
        with admin.begin() as db:
            db.execute(
                text(
                    "DELETE FROM forge.role_permissions WHERE organization_id=:org "
                    "AND role_id=:role AND permission_code=:permission"
                ),
                identity | {"permission": permission},
            )
    finally:
        admin.dispose()


@asynccontextmanager
async def foreign_client(identity):
    transport = httpx.ASGITransport(app=app, client=(uuid4().hex, 1234))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"Origin": settings().web_origin}
    ) as c:
        login = await create(
            c,
            "auth/login",
            {
                "organization_code": identity["code"],
                "email": "same@example.test",
                "password": "test-only-password-8472",
            },
        )
        assert login.status_code == 200, login.text
        yield c


@pytest.mark.parametrize("side", ["AR", "AP"])
@pytest.mark.parametrize("kind", ["SETTLEMENT", "REFUND"])
async def test_two_cash_documents_cannot_compete_past_the_same_source_balance(
    funds_case, side, kind
):
    c, party = funds_case["c"], funds_case[side]
    row = await source(c, party, side, "-10" if kind == "REFUND" else "10")
    body = cash_body(party, [(row, "7")], side, kind)
    results = await asyncio.wait_for(asyncio.gather(cash(c, body), cash(c, body)), timeout=10)
    assert sorted(r.status_code for r in results) == [201, 409], [r.text for r in results]
    state = await facts(funds_case["identity"])
    assert len(state["funds_cash_documents"]) == len(state["funds_cash_allocations"]) == 1
    assert state["funds_cash_documents"][0]["amount"] == 7
    detail = await source_detail(c, row)
    assert D(detail["balance"]) == (-3 if kind == "REFUND" else 3)
    assert D(detail["refund_amount"] if kind == "REFUND" else detail["settlement_amount"]) == 3


@pytest.mark.parametrize("side", ["AR", "AP"])
@pytest.mark.parametrize("kind", ["SETTLEMENT", "REFUND"])
@pytest.mark.parametrize("expiry", ["expired", "cleaned"])
async def test_permanent_cash_receipt_survives_ttl_cleanup_and_rejects_changed_body(
    funds_case, side, kind, expiry
):
    c, party = funds_case["c"], funds_case[side]
    row = await source(c, party, side, "-10" if kind == "REFUND" else "10")
    body = cash_body(party, [(row, "3.1234")], side, kind)
    key = uuid4().hex
    posted = await cash(c, body, key)
    assert posted.status_code == 201, posted.text
    assert set(posted.json()) == {"id", "status", "request_id"}
    before = await facts(funds_case["identity"])
    await expire(funds_case["identity"], key, expiry)
    results = await asyncio.wait_for(
        asyncio.gather(cash(c, body, key), cash(c, body, key)), timeout=10
    )
    assert all(r.status_code == 201 and r.json() == posted.json() for r in results)
    # Expire the recreated short-lived cache as well, to reach permanent hash validation.
    await expire(funds_case["identity"], key, expiry)
    changed = await cash(c, body | {"reason": "不能用原键换另一笔"}, key)
    assert changed.status_code == 409 and changed.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert await facts(funds_case["identity"]) == before


@pytest.mark.parametrize(
    "side,kind,permission",
    [
        ("AR", "SETTLEMENT", "funds.receive"),
        ("AP", "SETTLEMENT", "funds.pay"),
        ("AR", "REFUND", "funds.customer_refund"),
        ("AP", "REFUND", "funds.supplier_refund"),
    ],
)
@pytest.mark.parametrize("expiry", ["live", "cleaned"])
async def test_revoked_cash_permission_is_checked_before_live_or_permanent_replay(
    funds_case, side, kind, permission, expiry
):
    c, party = funds_case["c"], funds_case[side]
    row = await source(c, party, side, "-10" if kind == "REFUND" else "10")
    body = cash_body(party, [(row, "3")], side, kind)
    key = uuid4().hex
    posted = await cash(c, body, key)
    assert posted.status_code == 201, posted.text
    if expiry != "live":
        await expire(funds_case["identity"], key, expiry)
    before = await facts(funds_case["identity"])
    revoke(funds_case["identity"], permission)
    response = await cash(c, body, key)
    assert response.status_code == 403 and response.json()["code"] == "PERMISSION_DENIED"
    assert await facts(funds_case["identity"]) == before


@pytest.mark.parametrize("side", ["AR", "AP"])
@pytest.mark.parametrize("expiry", ["expired", "cleaned"])
async def test_reverse_receipt_is_permanent_without_reversing_cash_twice(funds_case, side, expiry):
    c, party = funds_case["c"], funds_case[side]
    row = await source(c, party, side)
    posted = await cash(c, cash_body(party, [(row, "3")], side))
    assert posted.status_code == 201, posted.text
    key = uuid4().hex
    reversed_response = await reverse_cash(c, posted.json(), key)
    assert reversed_response.status_code == 200, reversed_response.text
    before = await facts(funds_case["identity"])
    await expire(funds_case["identity"], key, expiry)
    replay = await reverse_cash(c, posted.json(), key)
    assert replay.status_code == 200 and replay.json() == reversed_response.json()
    assert await facts(funds_case["identity"]) == before
    assert D((await source_detail(c, row))["balance"]) == 10
    revoke(funds_case["identity"], "funds.reverse")
    forbidden = await reverse_cash(c, posted.json(), key)
    assert forbidden.status_code == 403 and forbidden.json()["code"] == "PERMISSION_DENIED"


@pytest.mark.parametrize("side", ["AR", "AP"])
async def test_cash_and_opening_reversal_serialize_without_lost_money(funds_case, side):
    c, party = funds_case["c"], funds_case[side]
    row = await source(c, party, side)
    paid, voided = await asyncio.wait_for(
        asyncio.gather(
            cash(c, cash_body(party, [(row, "7")], side)),
            create(c, "funds/sources/" + row["id"] + "/reverse", {"reason": "纠正期初"}),
        ),
        timeout=10,
    )
    assert (paid.status_code, voided.status_code) in {(201, 409), (409, 200)}, (
        paid.text,
        voided.text,
    )
    detail = await source_detail(c, row)
    state = await facts(funds_case["identity"])
    if paid.status_code == 201:
        assert D(detail["balance"]) == 3 and detail["status"] == "OPEN"
        assert len(state["funds_cash_documents"]) == 1
    else:
        assert D(detail["balance"]) == 0 and detail["status"] == "REVERSED"
        assert state["funds_cash_documents"] == []


@pytest.mark.parametrize(
    "marker",
    [
        "INSERT INTO forge.funds_cash_documents",
        "INSERT INTO forge.funds_cash_allocations",
        "INSERT INTO forge.audit_events",
        "INSERT INTO forge.outbox_events",
        "INSERT INTO forge.funds_operations",
        "INSERT INTO forge.idempotency_keys",
        "COMMIT",
    ],
)
async def test_cash_faults_roll_back_allocations_audit_outbox_and_permanent_receipt(
    funds_case, monkeypatch, marker
):
    c, party = funds_case["c"], funds_case["AR"]
    first, second = await source(c, party), await source(c, party)
    body = cash_body(party, [(first, "3.1234"), (second, "4.5678")])
    key = uuid4().hex
    before = await facts(funds_case["identity"])
    original = AsyncSession.execute
    injected = False

    async def broken(self, statement, *args, **kwargs):
        nonlocal injected
        result = await original(self, statement, *args, **kwargs)
        if marker in str(statement) and not injected:
            injected = True
            raise RuntimeError("funds injection marker")
        return result

    def fail_commit(session):
        nonlocal injected
        injected = True
        raise RuntimeError("funds commit injection")

    if marker == "COMMIT":
        event.listen(Session, "before_commit", fail_commit)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "execute", broken)
            response = await cash(c, body, key)
    finally:
        if marker == "COMMIT":
            event.remove(Session, "before_commit", fail_commit)
    assert injected and response.status_code == 500, response.text
    assert "injection" not in response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert await facts(funds_case["identity"]) == before
    retried = await cash(c, body, key)
    assert retried.status_code == 201, retried.text
    result = await facts(funds_case["identity"])
    assert len(result["funds_cash_documents"]) == 1
    assert len(result["funds_cash_allocations"]) == 2
    assert result["funds_cash_documents"][0]["amount"] == D("7.6912")


async def test_second_allocation_failure_rolls_back_the_first(funds_case, monkeypatch):
    c, party = funds_case["c"], funds_case["AR"]
    first, second = await source(c, party), await source(c, party)
    body = cash_body(party, [(first, "3"), (second, "4")])
    before = await facts(funds_case["identity"])
    original = AsyncSession.execute
    seen = 0

    async def broken(self, statement, *args, **kwargs):
        nonlocal seen
        result = await original(self, statement, *args, **kwargs)
        if "INSERT INTO forge.funds_cash_allocations" in str(statement):
            seen += 1
            if seen == 2:
                raise RuntimeError("second allocation failed")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", broken)
        response = await cash(c, body)
    assert seen == 2 and response.status_code == 500, response.text
    assert await facts(funds_case["identity"]) == before


async def clone_row(db, table, row, overrides):
    columns = [field for field in row if field != "sequence"]
    values = [":" + field if field in overrides else field for field in columns]
    return await db.execute(
        text(
            f"INSERT INTO forge.{table} ({','.join(columns)}) SELECT {','.join(values)} "
            f"FROM forge.{table} WHERE id=:original"
        ),
        overrides | {"original": row["id"]},
    )


@pytest.mark.parametrize("side", ["AR", "AP"])
@pytest.mark.parametrize("table", ["funds_sources", "funds_cash_documents"])
async def test_nullable_party_columns_cannot_bypass_sql_check_constraints(funds_case, side, table):
    c, party = funds_case["c"], funds_case[side]
    row = await source(c, party, side)
    if table == "funds_cash_documents":
        response = await cash(c, cash_body(party, [(row, "1")], side))
        assert response.status_code == 201, response.text
    state = await facts(funds_case["identity"])
    original = next(x for x in state[table] if x["side"] == side)
    with pytest.raises(DBAPIError) as caught:
        async with sessions.begin() as db:
            await set_tenant(db, funds_case["identity"]["org"])
            await clone_row(
                db,
                table,
                original,
                {
                    "id": uuid4(),
                    "number": "INVALID-" + uuid4().hex,
                    "customer_id" if side == "AR" else "supplier_id": None,
                },
            )
    assert getattr(caught.value.orig, "sqlstate", None) == "23514"
    assert await facts(funds_case["identity"]) == state


async def test_all_funds_tables_enforce_tenant_reads_and_immutable_app_facts(
    funds_case, identities
):
    c, party = funds_case["c"], funds_case["AR"]
    row = await source(c, party)
    posted = await cash(c, cash_body(party, [(row, "3")]))
    assert posted.status_code == 201, posted.text
    assert (await reverse_cash(c, posted.json())).status_code == 200
    state = await facts(identities[0])
    assert all(state[table] for table in TABLES)
    async with sessions.begin() as db:
        await set_tenant(db, identities[1]["org"])
        for table in TABLES:
            assert (await db.execute(text(f"SELECT count(*) FROM forge.{table}"))).scalar_one() == 0
    async with sessions.begin() as db:
        for table in TABLES:
            assert (await db.execute(text(f"SELECT count(*) FROM forge.{table}"))).scalar_one() == 0
    for table in TABLES:
        for operation in ("UPDATE", "DELETE"):
            with pytest.raises(DBAPIError) as caught:
                async with sessions.begin() as db:
                    await set_tenant(db, identities[0]["org"])
                    statement = (
                        f"UPDATE forge.{table} SET id=id"
                        if operation == "UPDATE"
                        else f"DELETE FROM forge.{table}"
                    )
                    await db.execute(
                        text(statement + " WHERE id=:id"), {"id": state[table][0]["id"]}
                    )
            assert getattr(caught.value.orig, "sqlstate", None) in {"42501", "23514"}
    assert await facts(identities[0]) == state


async def test_foreign_funds_sources_and_cash_are_not_readable_or_allocatable(
    funds_case, identities
):
    c, party = funds_case["c"], funds_case["AR"]
    async with foreign_client(identities[1]) as other:
        await enable(other)
        customer = await create(other, "customers", {"code": "OTHER", "name": "别租户客户"})
        other_party = customer.json()["id"]
        other_source = await source(other, other_party)
        other_cash = await cash(other, cash_body(other_party, [(other_source, "2")]))
        assert other_cash.status_code == 201, other_cash.text
    before, foreign_before = await facts(identities[0]), await facts(identities[1])
    for path in ("sources/" + other_source["id"], "cash/" + other_cash.json()["id"]):
        denied = await c.get("/api/v1/funds/" + path)
        assert denied.status_code == 404, denied.text
    denied = await cash(c, cash_body(party, [(other_source, "1")]))
    assert denied.status_code in {404, 409}, denied.text
    denied = await cash(c, cash_body(other_party, [(other_source, "1")]))
    assert denied.status_code == 404, denied.text
    assert await facts(identities[0]) == before
    assert await facts(identities[1]) == foreign_before


async def business_facts(identity):
    result = await facts(identity)
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        for table in (
            "inventory_documents",
            "inventory_document_lines",
            "inventory_movements",
            "inventory_balances",
            "inventory_reservations",
            "inventory_reversals",
            "sales_orders",
            "sales_order_lines",
            "sales_documents",
            "sales_document_lines",
            "purchase_orders",
            "purchase_order_lines",
            "purchase_documents",
            "purchase_document_lines",
            "idempotency_keys",
            "audit_events",
            "outbox_events",
        ):
            rows = (
                await db.execute(
                    text(f"SELECT * FROM forge.{table} WHERE organization_id=:org"), identity
                )
            ).mappings()
            result[table] = sorted((committed_fact(table, row) for row in rows), key=str)
    return result


async def test_fact_snapshot_tracks_committed_events_but_ignores_worker_delivery(funds_case):
    case = funds_case
    before = await business_facts(case["identity"])
    assert before["outbox_events"]
    async with sessions.begin() as db:
        await set_tenant(db, case["identity"]["org"])
        await db.execute(
            text(
                "UPDATE forge.outbox_events SET processed_at=clock_timestamp(), "
                "attempts=attempts+1 WHERE organization_id=:org"
            ),
            case["identity"],
        )
    assert await business_facts(case["identity"]) == before
    assert (
        await create(case["c"], "customers", {"code": "AFTER-DELIVERY", "name": "新增事件"})
    ).status_code == 201
    after = await business_facts(case["identity"])
    assert len(after["outbox_events"]) == len(before["outbox_events"]) + 1
    assert after != before


async def commercial_documents(c, sale, side, count=1):
    """Create actual posted commercial facts worth 45 each, before or after cutover."""
    if side == "AR":
        order, first = await issued(c, sale)
        party = sale["customer_id"]
    else:
        supplier = await create(c, "suppliers", {"code": "FUNDS-COM", "name": "原单资金供应商"})
        assert supplier.status_code == 201, supplier.text
        party = supplier.json()["id"]
        order = await po(
            c,
            {
                "supplier_id": party,
                "warehouse_id": sale["warehouse_id"],
                "reason": "资金安全实际收货",
                "lines": [{k: v for k, v in sale["lines"][0].items() if k != "pricing_mode"}],
            },
        )
        confirmed = await purchase_action(c, order)
        assert confirmed.status_code == 200, confirmed.text
        first, _ = await receive(c, order, "3")
        posted = await purchase_action(c, first, "post", document=True)
        assert posted.status_code == 200, posted.text
        first = posted.json()
    documents = [first]
    if count == 2:
        if side == "AR":
            second, _ = await shipment(c, order, "3")
            posted = await post_shipment(c, second)
        else:
            second, _ = await receive(c, order, "3")
            posted = await purchase_action(c, second, "post", document=True)
        assert posted.status_code == 200, posted.text
        documents.append(posted.json())
    return party, documents


async def business_reverse(c, row, side):
    if side == "AR":
        return await reverse(c, row)
    return await purchase_action(c, row, "reverse", "资金依赖冲销", document=True)


async def commercial_return(c, row, side, qty="1"):
    draft, _ = await (return_draft(c, row, qty) if side == "AR" else purchase_return(c, row, qty))
    return draft


async def post_commercial(c, row, side, key=None):
    if side == "AR":
        return await post_shipment(c, row, key=key)
    return await purchase_action(c, row, "post", document=True, key=key)


async def mapped_source(c, document, side):
    response = await c.get("/api/v1/funds/sources", params={"side": side})
    assert response.status_code == 200, response.text
    return next(
        row for row in response.json()["items"] if row["source_document_id"] == document["id"]
    )


def binding_body(document, opening, side, amount="30"):
    return {
        "side": side,
        "source_document_id": document["id"],
        "opening_source_id": opening["id"] if opening else None,
        "amount": amount,
        "reason": "明确旧单期初来源转移",
    }


@pytest.mark.parametrize("side", ["AR", "AP"])
async def test_cash_and_original_commercial_reversal_have_one_consistent_winner(
    catalog_client, sale, identities, side
):
    c = catalog_client
    await enable(c)
    party, documents = await commercial_documents(c, sale, side)
    row = await mapped_source(c, documents[0], side)
    paid, reversed_document = await asyncio.wait_for(
        asyncio.gather(
            cash(c, cash_body(party, [(row, "20")], side)),
            business_reverse(c, documents[0], side),
        ),
        timeout=10,
    )
    assert (paid.status_code, reversed_document.status_code) in {(201, 409), (409, 200)}, (
        paid.text,
        reversed_document.text,
    )
    detail = await source_detail(c, row)
    state = await facts(identities[0])
    if paid.status_code == 201:
        assert reversed_document.json()["code"] == "FUNDS_DEPENDENCY_CONFLICT"
        assert D(detail["balance"]) == 25
        assert len(state["funds_cash_documents"]) == 1
    else:
        assert D(detail["balance"]) == 0
        assert state["funds_cash_documents"] == []
    stock = await c.get("/api/v1/inventory/documents/" + documents[0]["id"])
    assert stock.status_code == 200, stock.text
    assert stock.json()["status"] == ("POSTED" if paid.status_code == 201 else "REVERSED")


@pytest.mark.parametrize("side", ["AR", "AP"])
async def test_refund_race_with_original_cash_reversal_preserves_its_credit_dependency(
    catalog_client, sale, identities, side
):
    c = catalog_client
    await enable(c)
    party, documents = await commercial_documents(c, sale, side)
    row = await mapped_source(c, documents[0], side)
    paid = await cash(c, cash_body(party, [(row, "45")], side))
    assert paid.status_code == 201, paid.text
    returned = await commercial_return(c, documents[0], side)
    assert (await post_commercial(c, returned, side)).status_code == 200
    assert D((await source_detail(c, row))["refund_amount"]) == 15
    refunded, reversed_cash = await asyncio.wait_for(
        asyncio.gather(
            cash(c, cash_body(party, [(row, "15")], side, "REFUND")),
            reverse_cash(c, paid.json()),
        ),
        timeout=10,
    )
    assert (refunded.status_code, reversed_cash.status_code) in {(201, 409), (409, 200)}, (
        refunded.text,
        reversed_cash.text,
    )
    detail = await source_detail(c, row)
    if refunded.status_code == 201:
        assert reversed_cash.json()["code"] == "FUNDS_REVERSAL_DEPENDENCY"
        assert D(detail["balance"]) == 0
    else:
        assert D(detail["balance"]) == 30
    state = await facts(identities[0])
    assert len(state["funds_cash_documents"]) == (2 if refunded.status_code == 201 else 1)
    assert len(state["funds_cash_reversals"]) == (0 if refunded.status_code == 201 else 1)


@pytest.mark.parametrize("side", ["AR", "AP"])
async def test_legacy_mapping_competing_keys_transfer_once_without_new_debt(
    catalog_client, sale, identities, side
):
    c = catalog_client
    party, documents = await commercial_documents(c, sale, side)
    assert (await facts(identities[0]))["funds_sources"] == []
    await enable(c)
    opening = await source(c, party, side, "30")
    body = binding_body(documents[0], opening, side)
    results = await asyncio.wait_for(
        asyncio.gather(
            create(c, "funds/legacy-bindings", body), create(c, "funds/legacy-bindings", body)
        ),
        timeout=10,
    )
    assert sorted(row.status_code for row in results) == [201, 409], [r.text for r in results]
    losing = next(r for r in results if r.status_code == 409)
    assert losing.json()["code"] == "FUNDS_SOURCE_ALREADY_MAPPED"
    transferred = await mapped_source(c, documents[0], side)
    assert D(transferred["balance"]) == 30
    assert D((await source_detail(c, opening))["balance"]) == 0
    state = await facts(identities[0])
    assert len(state["funds_sources"]) == 2
    assert sum(row["amount"] for row in state["funds_entries"]) == 30
    assert sum(row["kind"] == "TRANSFER_OUT" for row in state["funds_entries"]) == 1
    denied = await create(c, "funds/sources/" + opening["id"] + "/reverse", {"reason": "不能双退"})
    assert denied.status_code == 409 and denied.json()["code"] == "FUNDS_REVERSAL_DEPENDENCY"


@pytest.mark.parametrize("side", ["AR", "AP"])
async def test_legacy_sources_compete_for_remaining_unsettled_opening_balance(
    catalog_client, sale, identities, side
):
    c = catalog_client
    party, documents = await commercial_documents(c, sale, side, 2)
    await enable(c)
    opening = await source(c, party, side, "60")
    paid = await cash(c, cash_body(party, [(opening, "10")], side))
    assert paid.status_code == 201, paid.text
    results = await asyncio.wait_for(
        asyncio.gather(
            *[
                create(c, "funds/legacy-bindings", binding_body(row, opening, side, "40"))
                for row in documents
            ]
        ),
        timeout=10,
    )
    assert sorted(row.status_code for row in results) == [201, 409], [r.text for r in results]
    assert (
        next(r for r in results if r.status_code == 409).json()["code"]
        == "OPENING_BALANCE_EXCEEDED"
    )
    assert D((await source_detail(c, opening))["balance"]) == 10
    rows = (await c.get("/api/v1/funds/sources", params={"side": side})).json()["items"]
    assert sum(D(row["balance"]) for row in rows) == 50
    state = await facts(identities[0])
    assert sum(row["amount"] for row in state["funds_entries"]) == 60
    assert len(state["funds_cash_documents"]) == 1


@pytest.mark.parametrize("side", ["AR", "AP"])
@pytest.mark.parametrize("operation", ["post_return", "reverse_original"])
async def test_unmapped_legacy_operations_do_not_leave_partial_inventory_facts(
    catalog_client, sale, identities, side, operation
):
    c = catalog_client
    party, documents = await commercial_documents(c, sale, side)
    await enable(c)
    pending = (
        await commercial_return(c, documents[0], side)
        if operation == "post_return"
        else documents[0]
    )
    before = await business_facts(identities[0])
    result = await (
        post_commercial(c, pending, side)
        if operation == "post_return"
        else business_reverse(c, pending, side)
    )
    assert result.status_code == 409 and result.json()["code"] == "LEGACY_FUNDS_MAPPING_REQUIRED"
    assert await business_facts(identities[0]) == before
    bound = await create(c, "funds/legacy-bindings", binding_body(documents[0], None, side, "0"))
    assert bound.status_code == 201, bound.text
    retried = await (
        post_commercial(c, pending, side)
        if operation == "post_return"
        else business_reverse(c, pending, side)
    )
    assert retried.status_code == 200, retried.text
    assert D((await source_detail(c, bound.json()))["balance"]) == (
        -15 if operation == "post_return" else -45
    )
    # Explicit zero mapping records historical paid funds; neither mapping nor return creates cash.
    assert (await facts(identities[0]))["funds_cash_documents"] == []


@pytest.mark.parametrize("side", ["AR", "AP"])
@pytest.mark.parametrize("marker", ["TRANSFER_OUT", "funds.legacy.bind", "COMMIT"])
async def test_legacy_transfer_faults_roll_back_both_sources_and_retry_original_intent(
    catalog_client, sale, identities, monkeypatch, side, marker
):
    c = catalog_client
    party, documents = await commercial_documents(c, sale, side)
    await enable(c)
    opening = await source(c, party, side, "30")
    body = binding_body(documents[0], opening, side)
    key = uuid4().hex
    before = await business_facts(identities[0])
    original = AsyncSession.execute
    injected = False

    async def broken(self, statement, *args, **kwargs):
        nonlocal injected
        result = await original(self, statement, *args, **kwargs)
        values = args[0] if args else kwargs.get("params", {})
        matches = (marker == "TRANSFER_OUT" and values.get("kind") == marker) or (
            marker == "funds.legacy.bind"
            and "INSERT INTO forge.audit_events" in str(statement)
            and values.get("action") == marker
        )
        if matches and not injected:
            injected = True
            raise RuntimeError("legacy transfer injection")
        return result

    def fail_commit(session):
        nonlocal injected
        injected = True
        raise RuntimeError("legacy commit injection")

    if marker == "COMMIT":
        event.listen(Session, "before_commit", fail_commit)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "execute", broken)
            result = await create(c, "funds/legacy-bindings", body, key)
    finally:
        if marker == "COMMIT":
            event.remove(Session, "before_commit", fail_commit)
    assert injected and result.status_code == 500, result.text
    assert await business_facts(identities[0]) == before
    retried = await create(c, "funds/legacy-bindings", body, key)
    assert retried.status_code == 201, retried.text
    committed = await facts(identities[0])
    await expire(identities[0], key, "cleaned")
    assert (await create(c, "funds/legacy-bindings", body, key)).json() == retried.json()
    assert await facts(identities[0]) == committed


@pytest.mark.parametrize("side", ["AR", "AP"])
@pytest.mark.parametrize("marker", ["funds_entries", "funds.source.return", "COMMIT"])
async def test_commercial_return_and_financial_credit_roll_back_together(
    catalog_client, sale, identities, monkeypatch, side, marker
):
    c = catalog_client
    await enable(c)
    _, documents = await commercial_documents(c, sale, side)
    row = await commercial_return(c, documents[0], side)
    before = await business_facts(identities[0])
    original = AsyncSession.execute
    injected = False

    async def broken(self, statement, *args, **kwargs):
        nonlocal injected
        result = await original(self, statement, *args, **kwargs)
        values = args[0] if args else kwargs.get("params", {})
        matches = (
            marker == "funds_entries" and "INSERT INTO forge.funds_entries" in str(statement)
        ) or (
            marker == "funds.source.return"
            and "INSERT INTO forge.outbox_events" in str(statement)
            and values.get("action") == marker
        )
        if matches and not injected:
            injected = True
            raise RuntimeError("commercial funds credit injection")
        return result

    def fail_commit(session):
        nonlocal injected
        injected = True
        raise RuntimeError("commercial credit commit injection")

    if marker == "COMMIT":
        event.listen(Session, "before_commit", fail_commit)
    key = uuid4().hex
    try:
        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "execute", broken)
            response = await post_commercial(c, row, side, key)
    finally:
        if marker == "COMMIT":
            event.remove(Session, "before_commit", fail_commit)
    assert injected and response.status_code == 500, response.text
    assert await business_facts(identities[0]) == before
    retried = await post_commercial(c, row, side, key)
    assert retried.status_code == 200, retried.text
    source_row = await mapped_source(c, documents[0], side)
    assert D(source_row["balance"]) == 30
    credits = [
        entry
        for entry in (await facts(identities[0]))["funds_entries"]
        if entry["kind"] == "RETURN"
    ]
    assert len(credits) == 1 and credits[0]["amount"] == -15


@pytest.mark.parametrize("side", ["AR", "AP"])
async def test_cash_action_permission_does_not_grant_reads_or_depend_on_read_permission(
    funds_case, side
):
    c, party = funds_case["c"], funds_case[side]
    row = await source(c, party, side)
    key = uuid4().hex
    body = cash_body(party, [(row, "3")], side)
    first = await cash(c, body, key)
    assert first.status_code == 201, first.text
    await expire(funds_case["identity"], key, "cleaned")
    revoke(funds_case["identity"], "funds.ar.read" if side == "AR" else "funds.ap.read")
    # An actor still authorized to record cash can resolve their own minimal receipt.
    replayed = await cash(c, body, key)
    assert replayed.status_code == 201 and replayed.json() == first.json()
    preview = await create(c, "funds/cash/preview", body)
    assert preview.status_code == 403, preview.text
    second = await cash(c, body)
    assert second.status_code == 201, second.text
    assert set(second.json()) == {"id", "status", "request_id"}
    for path in ("sources/" + row["id"], "cash/" + first.json()["id"]):
        response = await c.get("/api/v1/funds/" + path)
        assert response.status_code == 403, response.text
    for path in ("sources", "cash", "summary", "parties"):
        response = await c.get("/api/v1/funds/" + path, params={"side": side})
        assert response.status_code == 403, response.text


async def test_invalidated_runtime_context_cannot_replay_permanent_cash_receipt(funds_case):
    c, party = funds_case["c"], funds_case["AR"]
    row = await source(c, party)
    key = uuid4().hex
    body = cash_body(party, [(row, "3")])
    assert (await cash(c, body, key)).status_code == 201
    await expire(funds_case["identity"], key, "cleaned")
    before = await facts(funds_case["identity"])
    admin = create_engine(settings().migration_database_url)
    try:
        with admin.begin() as db:
            db.execute(
                text("UPDATE forge.users SET active=false WHERE organization_id=:org AND id=:user"),
                funds_case["identity"],
            )
    finally:
        admin.dispose()
    response = await cash(c, body, key)
    assert response.status_code == 401, response.text
    assert await facts(funds_case["identity"]) == before


async def test_rls_blocks_direct_cross_tenant_insert_into_every_funds_table(funds_case, identities):
    c, party = funds_case["c"], funds_case["AR"]
    row = await source(c, party)
    paid = await cash(c, cash_body(party, [(row, "2")]))
    assert paid.status_code == 201, paid.text
    assert (await reverse_cash(c, paid.json())).status_code == 200
    before = await facts(identities[0])
    for table in TABLES:
        with pytest.raises(DBAPIError) as caught:
            async with sessions.begin() as db:
                await set_tenant(db, identities[0]["org"])
                await clone_row(
                    db,
                    table,
                    before[table][0],
                    {"id": uuid4(), "organization_id": identities[1]["org"]},
                )
        assert getattr(caught.value.orig, "sqlstate", None) == "42501", table
    assert await facts(identities[0]) == before
    assert all(not rows for rows in (await facts(identities[1])).values())


@pytest.mark.parametrize("mismatch", ["party", "side", "tenant"])
async def test_cash_allocation_composite_references_reject_mismatched_source(
    funds_case, identities, mismatch
):
    c, party = funds_case["c"], funds_case["AR"]
    opening = await source(c, party)
    paid = await cash(c, cash_body(party, [(opening, "2")]))
    assert paid.status_code == 201, paid.text
    if mismatch == "party":
        other = await create(c, "customers", {"code": "SAME-TENANT-OTHER", "name": "另一客户"})
        invalid_source = await source(c, other.json()["id"])
    elif mismatch == "side":
        invalid_source = await source(c, funds_case["AP"], "AP")
    else:
        async with foreign_client(identities[1]) as other:
            await enable(other)
            customer = await create(other, "customers", {"code": "OTHER-SOURCE", "name": "别租户"})
            invalid_source = await source(other, customer.json()["id"])
    before = await facts(identities[0])
    with pytest.raises(DBAPIError) as caught:
        async with sessions.begin() as db:
            await set_tenant(db, identities[0]["org"])
            await clone_row(
                db,
                "funds_cash_allocations",
                before["funds_cash_allocations"][0],
                {"id": uuid4(), "source_id": invalid_source["id"]},
            )
    assert getattr(caught.value.orig, "sqlstate", None) == "23503"
    assert await facts(identities[0]) == before


@pytest.mark.parametrize(
    "operation,permission", [("openings", "funds.opening"), ("adjustments", "funds.adjust")]
)
async def test_opening_and_adjustment_permanent_receipts_survive_reversal_without_new_balance(
    funds_case, operation, permission
):
    c, party = funds_case["c"], funds_case["AR"]
    body = {"side": "AR", "party_id": party, "amount": "2.1234", "reason": "期初调整唯一意图"}
    key = uuid4().hex
    first = await create(c, "funds/" + operation, body, key)
    assert first.status_code == 201, first.text
    reversed_source = await create(
        c, "funds/sources/" + first.json()["id"] + "/reverse", {"reason": "后续纠正"}
    )
    assert reversed_source.status_code == 200, reversed_source.text
    await expire(funds_case["identity"], key, "cleaned")
    before = await facts(funds_case["identity"])
    replayed = await create(c, "funds/" + operation, body, key)
    assert replayed.status_code == 201 and replayed.json() == first.json()
    assert D((await source_detail(c, first.json()))["balance"]) == 0
    await expire(funds_case["identity"], key, "expired")
    changed = await create(c, "funds/" + operation, body | {"amount": "3"}, key)
    assert changed.status_code == 409 and changed.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    revoke(funds_case["identity"], permission)
    denied = await create(c, "funds/" + operation, body, key)
    assert denied.status_code == 403, denied.text
    assert await facts(funds_case["identity"]) == before


async def test_activation_permanent_receipt_preserves_original_cutover_and_permission(
    catalog_client, identities
):
    c = catalog_client
    body = {"business_date": datetime.now(UTC).date().isoformat(), "reason": "首次启用"}
    key = uuid4().hex
    first = await create(c, "funds/activate", body, key)
    assert first.status_code == 201, first.text
    await expire(identities[0], key, "cleaned")
    before = await facts(identities[0])
    replayed = await create(c, "funds/activate", body, key)
    assert replayed.status_code == 201 and replayed.json() == first.json()
    await expire(identities[0], key, "expired")
    changed = await create(c, "funds/activate", body | {"reason": "修改切点意图"}, key)
    assert changed.status_code == 409 and changed.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    revoke(identities[0], "funds.activate")
    denied = await create(c, "funds/activate", body, key)
    assert denied.status_code == 403, denied.text
    assert await facts(identities[0]) == before


@pytest.mark.parametrize("side", ["AR", "AP"])
async def test_committed_cash_receipt_remains_original_after_cash_later_reversed(funds_case, side):
    c, party = funds_case["c"], funds_case[side]
    row = await source(c, party, side)
    body = cash_body(party, [(row, "3")], side)
    key = uuid4().hex
    first = await cash(c, body, key)
    assert first.status_code == 201, first.text
    assert (await reverse_cash(c, first.json())).status_code == 200
    await expire(funds_case["identity"], key, "cleaned")
    before = await facts(funds_case["identity"])
    replayed = await cash(c, body, key)
    assert replayed.status_code == 201 and replayed.json() == first.json()
    assert replayed.json()["status"] == "POSTED"
    current = await c.get("/api/v1/funds/cash/" + first.json()["id"])
    assert current.status_code == 200 and current.json()["status"] == "REVERSED"
    assert D((await source_detail(c, row))["balance"]) == 10
    assert await facts(funds_case["identity"]) == before


@pytest.mark.parametrize("side", ["AR", "AP"])
@pytest.mark.parametrize("activation_first", [True, False])
async def test_cutover_lock_orders_real_posting_and_activation_without_timestamp_guessing(
    catalog_client, sale, identities, monkeypatch, side, activation_first
):
    c = catalog_client
    _, documents = await commercial_documents(c, sale, side)
    prefix = "sales" if side == "AR" else "purchasing"
    original = await c.get(f"/api/v1/{prefix}/documents/" + documents[0]["id"])
    assert original.status_code == 200, original.text
    order = {"id": original.json()["order_id"]}
    pending, _ = await (shipment(c, order, "1") if side == "AR" else receive(c, order, "1"))
    held = asyncio.Event()
    competitor_attempted = asyncio.Event()
    release = asyncio.Event()
    original_cutover = funds_shared.cutover
    original_execute = AsyncSession.execute
    intercepted = False

    async def controlled(db, ctx, *, exclusive=False, required=False):
        nonlocal intercepted
        result = await original_cutover(db, ctx, exclusive=exclusive, required=required)
        if exclusive == activation_first and not intercepted:
            intercepted = True
            # Both branches hold an actual advisory lock while no activation row exists.
            assert result is None
            held.set()
            await asyncio.wait_for(release.wait(), timeout=10)
        return result

    async def observe_lock(self, statement, *args, **kwargs):
        values = args[0] if args else kwargs.get("params", {})
        if (
            str(values.get("key", "")).startswith("funds-cutover:")
            and "pg_advisory_xact_lock" in str(statement)
            and held.is_set()
            and not release.is_set()
        ):
            # Business entrypoints intentionally import cutover directly; observe
            # their actual SQL rather than only the later integration hook.
            competitor_attempted.set()
        return await original_execute(self, statement, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(funds_shared, "cutover", controlled)
        patch.setattr(AsyncSession, "execute", observe_lock)
        first = asyncio.create_task(
            enable(c) if activation_first else post_commercial(c, pending, side)
        )
        second = None
        try:
            await asyncio.wait_for(held.wait(), timeout=10)
            second = asyncio.create_task(
                post_commercial(c, pending, side) if activation_first else enable(c)
            )
            await asyncio.wait_for(competitor_attempted.wait(), timeout=10)
            async with asyncio.timeout(3), sessions.begin() as observer:
                # PostgreSQL's wait queue has no in-process event to await.
                while not (  # noqa: ASYNC110
                    await observer.execute(
                        text(
                            "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' "
                            "AND NOT granted AND pid<>pg_backend_pid() AND database="
                            "(SELECT oid FROM pg_database WHERE datname=current_database()))"
                        )
                    )
                ).scalar_one():
                    await asyncio.sleep(0.01)
            assert not first.done() and not second.done()
            release.set()
            results = await asyncio.wait_for(asyncio.gather(first, second), timeout=10)
        finally:
            release.set()
            for task in (first, second):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *[t for t in (first, second) if t is not None], return_exceptions=True
            )
    posted = results[1] if activation_first else results[0]
    assert posted.status_code == 200, posted.text
    state = await facts(identities[0])
    assert len(state["funds_activation"]) == 1
    sources = state["funds_sources"]
    assert len(sources) == (1 if activation_first else 0)
    if activation_first:
        assert str(sources[0]["source_document_id"]) == pending["id"]
        assert sum(row["amount"] for row in state["funds_entries"]) == 15
    # Regardless of timestamp, the next posting after committed activation is a new source.
    next_draft, _ = await (shipment(c, order, "1") if side == "AR" else receive(c, order, "1"))
    assert (await post_commercial(c, next_draft, side)).status_code == 200
    assert D((await mapped_source(c, next_draft, side))["balance"]) == 15


@pytest.mark.parametrize("side", ["AR", "AP"])
async def test_negative_legacy_mappings_share_refund_capacity_and_keep_durable_receipts(
    catalog_client, sale, identities, side
):
    c = catalog_client
    party, documents = await commercial_documents(c, sale, side, 2)
    for original in documents:
        returned = await commercial_return(c, original, side)
        result = await post_commercial(c, returned, side)
        assert result.status_code == 200, result.text
    await enable(c)
    opening = await source(c, party, side, "-20")
    refunded = await cash(c, cash_body(party, [(opening, "5")], side, "REFUND"))
    assert refunded.status_code == 201, refunded.text
    bodies = [
        binding_body(original, opening, side, "-15") | {"refund_balance_confirmed": True}
        for original in documents
    ]
    before = await facts(identities[0])
    unconfirmed = await create(
        c, "funds/legacy-bindings", bodies[0] | {"refund_balance_confirmed": False}
    )
    assert unconfirmed.status_code == 422
    assert unconfirmed.json()["code"] == "REFUND_BALANCE_CONFIRMATION_REQUIRED"
    assert await facts(identities[0]) == before
    keys = [uuid4().hex, uuid4().hex]
    results = await asyncio.wait_for(
        asyncio.gather(
            *[
                create(c, "funds/legacy-bindings", body, key)
                for body, key in zip(bodies, keys, strict=True)
            ]
        ),
        timeout=10,
    )
    assert sorted(result.status_code for result in results) == [201, 409], [r.text for r in results]
    losing = next(result for result in results if result.status_code == 409)
    assert losing.json()["code"] == "OPENING_BALANCE_EXCEEDED"
    winner = next(index for index, result in enumerate(results) if result.status_code == 201)
    mapped = results[winner].json()
    assert D((await source_detail(c, opening))["balance"]) == 0
    assert D((await source_detail(c, mapped))["balance"]) == -15
    state = await facts(identities[0])
    assert sum(entry["amount"] for entry in state["funds_entries"]) == -20
    transfer = [entry for entry in state["funds_entries"] if entry["kind"] == "TRANSFER_OUT"]
    assert len(transfer) == 1 and transfer[0]["amount"] == 15
    final_refund = await cash(c, cash_body(party, [(mapped, "15")], side, "REFUND"))
    assert final_refund.status_code == 201, final_refund.text
    assert D((await source_detail(c, mapped))["balance"]) == 0
    await expire(identities[0], keys[winner], "cleaned")
    committed = await facts(identities[0])
    replayed = await create(c, "funds/legacy-bindings", bodies[winner], keys[winner])
    assert replayed.status_code == 201 and replayed.json() == mapped
    assert await facts(identities[0]) == committed
    assert sum(row["amount"] for row in committed["funds_cash_documents"]) == 20
