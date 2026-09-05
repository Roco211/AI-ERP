"""Independent real-PostgreSQL worker, transaction, retention and tenant regressions."""

import asyncio
from contextlib import asynccontextmanager
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from test_catalog import catalog_client as catalog_client
from test_catalog import create, product_fixture

from forge_erp.core.config import settings
from forge_erp.core.db import sessions, set_tenant
from forge_erp.modules.catalog_import.application import runner
from forge_erp.workers import catalog_import as worker

BASE = "/api/v1/catalog-imports"


async def upload(c, rows, resource="brands", headers=None):
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "资料"
    sheet.append(headers or ["编码", "名称", "备注"])
    for row in rows:
        sheet.append(row)
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    response = await c.post(
        BASE + "/previews",
        data={"resource": resource, "worksheet": "资料"},
        files={
            "file": (
                "safety.xlsx",
                stream.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def detail(c, batch):
    response = await c.get(BASE + "/" + batch["id"])
    assert response.status_code == 200, response.text
    return response.json()


async def rows(c, batch):
    response = await c.get(BASE + "/" + batch["id"] + "/rows", params={"page_size": 100})
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def confirmed(c, values=None, *, resource="brands", headers=None):
    batch = await upload(c, values or [["SAFE-A", "安全A", "保留原文"]], resource, headers)
    current = await detail(c, batch)
    assert current["invalid"] == 0, await rows(c, batch)
    body = {"expected_version": current["version"], "preview_hash": current["preview_hash"]}
    key = uuid4().hex
    response = await create(c, "catalog-imports/" + batch["id"] + "/confirm", body, key)
    assert response.status_code == 200, response.text
    return batch, body, key


async def retry(c, batch, row_ids):
    current = await detail(c, batch)
    return await create(
        c,
        "catalog-imports/" + batch["id"] + "/retry",
        {"expected_version": current["version"], "row_ids": row_ids},
    )


async def facts(identity):
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        brands = [
            dict(row)
            for row in (
                await db.execute(
                    text("SELECT id,code,name,version FROM forge.brands ORDER BY code")
                )
            ).mappings()
        ]
        audits = [
            dict(row)
            for row in (
                await db.execute(
                    text(
                        "SELECT action,actor_type,actor_id,source,request_id,after "
                        "FROM forge.audit_events WHERE action IN "
                        "('catalog.brands.create','catalog.import.row.succeeded') ORDER BY id"
                    )
                )
            ).mappings()
        ]
        outbox = (
            await db.execute(
                text(
                    "SELECT count(*) FROM forge.outbox_events WHERE event_type IN "
                    "('catalog.brands.create','catalog.import.row.succeeded')"
                )
            )
        ).scalar_one()
        return {"brands": brands, "audits": audits, "outbox": outbox}


def expire(identity, batch):
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(text("SET LOCAL session_replication_role=replica"))
        db.execute(
            text(
                "UPDATE forge.import_batches SET expires_at=clock_timestamp()-interval '1 second' "
                "WHERE organization_id=:org AND id=:id"
            ),
            identity | {"id": batch["id"]},
        )


async def test_concurrent_workers_claim_distinct_rows_once_and_keep_actor_audit(
    catalog_client, identities
):
    c, identity = catalog_client, identities[0]
    batch, _, _ = await confirmed(c, [[f"SAFE-{i}", f"品牌{i}", ""] for i in range(6)])
    outcomes = await asyncio.wait_for(
        asyncio.gather(*[runner.run_one(sessions, identity["org"]) for _ in range(12)]), 15
    )
    assert outcomes.count("SUCCEEDED") == 6 and outcomes.count(None) == 6
    assert (await detail(c, batch))["status"] == "COMPLETED"
    result = await facts(identity)
    assert len(result["brands"]) == 6 and len(result["audits"]) == result["outbox"] == 12
    assert all(
        a["actor_type"] == "USER" and a["actor_id"] == identity["user"] and a["source"] == "API"
        for a in result["audits"]
    )
    assert all(row["attempts"] == 1 and row["target_version"] == 1 for row in await rows(c, batch))
    assert await runner.run_one(sessions, identity["org"]) is None


@pytest.mark.parametrize("marker", ["business_audit", "row_status", "row_audit", "idempotency"])
async def test_row_failure_rolls_back_business_audit_outbox_receipt_and_can_retry(
    catalog_client, identities, monkeypatch, marker
):
    c, identity = catalog_client, identities[0]
    batch, _, _ = await confirmed(c)
    original = AsyncSession.execute
    injected = False

    async def broken(self, statement, *args, **kwargs):
        nonlocal injected
        result = await original(self, statement, *args, **kwargs)
        sql = str(statement)
        params = args[0] if args and isinstance(args[0], dict) else {}
        match = (
            marker == "business_audit"
            and "INSERT INTO forge.outbox_events" in sql
            and params.get("action") == "catalog.brands.create"
        )
        match |= marker == "row_status" and "status='SUCCEEDED'" in sql
        match |= (
            marker == "row_audit"
            and "INSERT INTO forge.outbox_events" in sql
            and params.get("action") == "catalog.import.row.succeeded"
        )
        match |= marker == "idempotency" and "INSERT INTO forge.idempotency_keys" in sql
        if match and not injected:
            injected = True
            raise RuntimeError("independent row transaction injection")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", broken)
        outcome = await runner.run_one(sessions, identity["org"])
    assert injected and outcome == "FAILED"
    assert await facts(identity) == {"brands": [], "audits": [], "outbox": 0}
    failed = (await rows(c, batch))[0]
    assert failed["status"] == "FAILED" and failed["target_id"] is None
    assert failed["error_code"] == "IMPORT_TEMPORARY_FAILURE" and failed["retryable"]
    assert (await retry(c, batch, [failed["id"]])).status_code == 200
    assert await runner.run_one(sessions, identity["org"]) == "SUCCEEDED"
    assert len((await facts(identity))["brands"]) == 1
    assert (await rows(c, batch))[0]["attempts"] == 2


@pytest.mark.parametrize("stage", ["before_commit", "after_commit"])
async def test_worker_commit_failure_or_lost_response_preserves_permanent_success(
    catalog_client, identities, stage
):
    c, identity = catalog_client, identities[0]
    batch, _, _ = await confirmed(c)
    injected = False

    def fail(session):
        nonlocal injected
        if not injected:
            injected = True
            raise RuntimeError("independent worker commit injection")

    @asynccontextmanager
    async def lost_response():
        async with sessions.begin() as db:
            yield db
        # Raise only after COMMIT and pool return, simulating the lost acknowledgement.
        fail(None)

    if stage == "before_commit":
        event.listen(Session, stage, fail)
    try:
        factory = sessions if stage == "before_commit" else SimpleNamespace(begin=lost_response)
        outcome = await runner.run_one(factory, identity["org"])
    finally:
        if stage == "before_commit":
            event.remove(Session, stage, fail)
    assert injected
    result = (await rows(c, batch))[0]
    if stage == "before_commit":
        assert outcome == "FAILED" and result["status"] == "FAILED"
        assert await facts(identity) == {"brands": [], "audits": [], "outbox": 0}
        assert (await retry(c, batch, [result["id"]])).status_code == 200
        assert await runner.run_one(sessions, identity["org"]) == "SUCCEEDED"
    else:
        assert outcome == "SUCCEEDED" and result["status"] == "SUCCEEDED"
    before = await facts(identity)
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(text("DELETE FROM forge.idempotency_keys WHERE organization_id=:org"), identity)
    assert await runner.run_one(sessions, identity["org"]) is None
    assert await facts(identity) == before


@pytest.mark.parametrize("cache_state", ["DELETED", "EXPIRED"])
async def test_confirm_replays_after_ttl_and_cannot_repeat_successful_rows(
    catalog_client, identities, cache_state
):
    c, identity = catalog_client, identities[0]
    batch, body, key = await confirmed(c)
    assert await runner.run_one(sessions, identity["org"]) == "SUCCEEDED"
    before = await facts(identity)
    with create_engine(settings().migration_database_url).begin() as db:
        sql = (
            "DELETE FROM forge.idempotency_keys WHERE organization_id=:org"
            if cache_state == "DELETED"
            else (
                "UPDATE forge.idempotency_keys "
                "SET expires_at=clock_timestamp()-interval '1 second' "
                "WHERE organization_id=:org"
            )
        )
        db.execute(text(sql), identity)
        if cache_state == "EXPIRED":
            assert (
                db.execute(
                    text(
                        "SELECT count(*) FROM forge.idempotency_keys "
                        "WHERE organization_id=:org AND expires_at<=clock_timestamp()"
                    ),
                    identity,
                ).scalar_one()
                > 0
            )
    replay = await create(c, "catalog-imports/" + batch["id"] + "/confirm", body, key)
    assert replay.status_code == 200 and replay.json()["status"] == "QUEUED"
    changed = await create(
        c, "catalog-imports/" + batch["id"] + "/confirm", body | {"expected_version": 9}, key
    )
    assert changed.status_code == 409 and changed.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    success = (await rows(c, batch))[0]
    assert (await retry(c, batch, [success["id"]])).status_code == 409
    assert await runner.run_one(sessions, identity["org"]) is None
    assert await facts(identity) == before


async def test_seven_day_cleanup_removes_bodies_but_keeps_success_proof_and_business(
    catalog_client, identities
):
    c, identity = catalog_client, identities[0]
    batch, _, _ = await confirmed(
        c, [["KEPT", "已完成", "保密原文"], ["PENDING", "未执行", "待清除"]]
    )
    assert await runner.run_one(sessions, identity["org"]) == "SUCCEEDED"
    before = await facts(identity)
    original = await rows(c, batch)
    expire(identity, batch)
    assert await runner.run_one(sessions, identity["org"]) is None
    assert await runner.purge(sessions, identity["org"]) == 2
    assert await runner.purge(sessions, identity["org"]) == 0
    view = await detail(c, batch)
    assert view["status"] == "EXPIRED" and not view["body_available"]
    cleaned = await rows(c, batch)
    assert cleaned[0]["target_id"] == original[0]["target_id"]
    assert cleaned[0]["status"] == "SUCCEEDED" and cleaned[0]["target_version"] == 1
    assert all(
        row["raw_values"] is None and row["cleaned_values"] is None and row["errors"] is None
        for row in cleaned
    )
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        stored = (
            (await db.execute(text("SELECT * FROM forge.import_rows ORDER BY row_no")))
            .mappings()
            .all()
        )
        assert all(
            row[field] is None
            for row in stored
            for field in (
                "raw_values",
                "cleaned_values",
                "command_values",
                "references_snapshot",
                "locator",
                "errors",
            )
        )
        assert all(row["content_hash"] and row["body_purged_at"] for row in stored)
    assert (await c.get(BASE + "/" + batch["id"] + "/result.xlsx")).status_code == 410
    assert (await retry(c, batch, [cleaned[1]["id"]])).status_code == 409
    assert await facts(identity) == before


@pytest.mark.parametrize(
    "change", ["import_permission", "catalog_permission", "user_inactive", "organization_inactive"]
)
async def test_queued_rows_use_current_actor_and_permissions(catalog_client, identities, change):
    c, identity = catalog_client, identities[0]
    batch, _, _ = await confirmed(c, [["ALLOWED", "先完成", ""], ["BLOCKED", "后阻断", ""]])
    assert await runner.run_one(sessions, identity["org"]) == "SUCCEEDED"
    with create_engine(settings().migration_database_url).begin() as db:
        if change.endswith("permission"):
            permission = (
                "catalog.import.write" if change == "import_permission" else "catalog.write"
            )
            db.execute(
                text(
                    "DELETE FROM forge.role_permissions WHERE organization_id=:org "
                    "AND permission_code=:permission"
                ),
                identity | {"permission": permission},
            )
        elif change == "user_inactive":
            db.execute(text("UPDATE forge.users SET active=false WHERE id=:user"), identity)
        else:
            db.execute(text("UPDATE forge.organizations SET active=false WHERE id=:org"), identity)
    assert await runner.run_one(sessions, identity["org"]) == "FAILED"
    result = await facts(identity)
    assert [row["code"] for row in result["brands"]] == ["ALLOWED"]
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        rows = (
            await db.execute(
                text("SELECT status,error_code FROM forge.import_rows ORDER BY row_no")
            )
        ).all()
        assert rows[0].status == "SUCCEEDED" and rows[1].status == "FAILED"
        assert rows[1].error_code == (
            "PERMISSION_DENIED" if change.endswith("permission") else "IMPORT_ACTOR_INACTIVE"
        )
    assert await runner.run_one(sessions, identity["org"]) is None


async def test_worker_rechecks_permission_after_waiting_for_catalog_lock(
    catalog_client, identities
):
    c, identity = catalog_client, identities[0]
    await confirmed(c)
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        blocker = (await db.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))"),
            {"scope": "catalog-write:" + str(identity["org"])},
        )
        work = asyncio.create_task(runner.run_one(sessions, identity["org"]))
        blocked = 0
        for _ in range(100):
            async with sessions.begin() as inspect:
                blocked = (
                    await inspect.execute(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE :pid=ANY(pg_blocking_pids(pid))"
                        ),
                        {"pid": blocker},
                    )
                ).scalar_one()
            if blocked:
                break
            await asyncio.sleep(0.02)
        assert blocked and not work.done()
        with create_engine(settings().migration_database_url).begin() as admin:
            admin.execute(
                text(
                    "DELETE FROM forge.role_permissions WHERE organization_id=:org "
                    "AND permission_code='catalog.write'"
                ),
                identity,
            )
    assert await asyncio.wait_for(work, 5) == "FAILED"
    assert await facts(identity) == {"brands": [], "audits": [], "outbox": 0}


async def test_expired_body_purge_skips_an_active_row_transaction(
    catalog_client, identities, monkeypatch
):
    c, identity = catalog_client, identities[0]
    batch, _, _ = await confirmed(c)
    reached, release = asyncio.Event(), asyncio.Event()
    original = runner.write_command

    async def pause(*args, **kwargs):
        result = await original(*args, **kwargs)
        reached.set()
        await asyncio.wait_for(release.wait(), 10)
        return result

    monkeypatch.setattr(runner, "write_command", pause)
    task = asyncio.create_task(runner.run_one(sessions, identity["org"]))
    await asyncio.wait_for(reached.wait(), 5)
    try:
        expire(identity, batch)
        assert await runner.purge(sessions, identity["org"]) == 0
    finally:
        release.set()
    assert await asyncio.wait_for(task, 5) == "SUCCEEDED"
    assert await runner.purge(sessions, identity["org"]) == 1
    assert len((await facts(identity))["brands"]) == 1


async def test_foreign_org_api_rls_and_composite_batch_reference(catalog_client, identities):
    c, first, other = catalog_client, identities[0], identities[1]
    batch, body, key = await confirmed(c)
    async with sessions.begin() as db:
        await set_tenant(db, other["org"])
        for table in ("import_batches", "import_rows"):
            assert (await db.execute(text(f"SELECT count(*) FROM forge.{table}"))).scalar() == 0
    assert await runner.run_one(sessions, other["org"]) is None
    response = await c.post(
        "/api/v1/auth/login",
        json={
            "organization_code": other["code"],
            "email": "same@example.test",
            "password": "test-only-password-8472",
        },
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert response.status_code == 200
    assert (await c.get(BASE + "/" + batch["id"])).status_code == 404
    assert (await c.get(BASE + "/" + batch["id"] + "/rows")).status_code == 404
    assert (
        await create(c, "catalog-imports/" + batch["id"] + "/confirm", body, key)
    ).status_code == 404
    assert (await c.get(BASE)).json()["total"] == 0
    with pytest.raises(DBAPIError):
        async with sessions.begin() as db:
            await set_tenant(db, other["org"])
            await db.execute(
                text(
                    "INSERT INTO forge.import_rows"
                    "(organization_id,batch_id,row_no,action,status,content_hash) "
                    "VALUES(:org,:batch,2,'CREATE','READY',repeat('a',64))"
                ),
                {"org": other["org"], "batch": UUID(batch["id"])},
            )
    with pytest.raises(DBAPIError):
        async with sessions.begin() as db:
            await set_tenant(db, other["org"])
            await db.execute(
                text(
                    "INSERT INTO forge.import_batches(organization_id,created_by,resource,mode,"
                    "filename,file_hash,worksheet,header_order,preview_hash) "
                    "VALUES(:org,:actor,'brands','CREATE_ONLY','x.xlsx',"
                    "repeat('a',64),'data',ARRAY['编码','名称'],repeat('b',64))"
                ),
                {"org": first["org"], "actor": first["user"]},
            )


@pytest.mark.parametrize(
    "mutation",
    [
        "DELETE FROM forge.import_rows",
        "DELETE FROM forge.import_batches",
        "UPDATE forge.import_rows SET status='PENDING',target_id=NULL,target_version=NULL",
        "UPDATE forge.import_rows SET target_version=2",
        "UPDATE forge.import_rows SET attempts=attempts+1",
        "UPDATE forge.import_batches SET confirmed_at=NULL,"
        "confirmation_key=NULL,confirmation_hash=NULL",
    ],
)
async def test_successful_receipts_and_confirmation_are_immutable(
    catalog_client, identities, mutation
):
    await confirmed(catalog_client)
    assert await runner.run_one(sessions, identities[0]["org"]) == "SUCCEEDED"
    with pytest.raises(DBAPIError):
        async with sessions.begin() as db:
            await set_tenant(db, identities[0]["org"])
            await db.execute(text(mutation))


async def test_success_requires_explicit_target_version_and_completion(catalog_client, identities):
    batch = await upload(catalog_client, [["VERSION", "版本约束", ""]])
    with pytest.raises(DBAPIError):
        async with sessions.begin() as db:
            await set_tenant(db, identities[0]["org"])
            await db.execute(
                text(
                    "INSERT INTO forge.import_rows(organization_id,batch_id,row_no,action,status,"
                    "content_hash,target_id,completed_at) "
                    "VALUES(:org,:batch,99,'CREATE','SUCCEEDED',repeat('a',64),:target,now())"
                ),
                {"org": identities[0]["org"], "batch": UUID(batch["id"]), "target": uuid4()},
            )


async def test_worker_refuses_administrative_connection_before_discovery(
    catalog_client, identities, monkeypatch
):
    await confirmed(catalog_client)
    misconfigured = settings().model_copy(
        update={"database_url": settings().migration_database_url}
    )
    monkeypatch.setattr(worker, "settings", lambda: misconfigured)
    with pytest.raises(RuntimeError, match="restricted forge_app"):
        await worker.run(identities[0]["org"])
    assert await facts(identities[0]) == {"brands": [], "audits": [], "outbox": 0}


async def test_product_base_unit_creation_failure_rolls_back_both_facts(
    catalog_client,
    identities,
    monkeypatch,
):
    c, identity = catalog_client, identities[0]
    assert (
        await create(c, "categories", {"code": "SAFE-CAT", "name": "安全分类"})
    ).status_code == 201
    assert (await create(c, "units", {"code": "SAFE-PCS", "name": "安全单位"})).status_code == 201
    batch, _, _ = await confirmed(
        c,
        [["IMPORTED-PRODUCT", "导入商品", "SAFE-CAT", "SAFE-PCS"]],
        resource="products",
        headers=["商品编码", "商品名称", "分类编码", "基础单位编码"],
    )
    original = AsyncSession.execute
    injected = False

    async def broken(self, statement, *args, **kwargs):
        nonlocal injected
        result = await original(self, statement, *args, **kwargs)
        if "INSERT INTO forge.product_units" in str(statement) and not injected:
            injected = True
            raise RuntimeError("automatic base conversion transaction injection")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", broken)
        assert await runner.run_one(sessions, identity["org"]) == "FAILED"
    assert injected
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        for table in ("products", "product_units"):
            assert (await db.execute(text(f"SELECT count(*) FROM forge.{table}"))).scalar() == 0
        assert (
            await db.execute(
                text(
                    "SELECT count(*) FROM forge.audit_events WHERE action='catalog.products.create'"
                )
            )
        ).scalar() == 0
        assert (
            await db.execute(
                text(
                    "SELECT count(*) FROM forge.outbox_events "
                    "WHERE event_type='catalog.products.create'"
                )
            )
        ).scalar() == 0
    row = (await rows(c, batch))[0]
    assert row["target_id"] is None and row["retryable"]
    assert (await retry(c, batch, [row["id"]])).status_code == 200
    assert await runner.run_one(sessions, identity["org"]) == "SUCCEEDED"
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        product = (await db.execute(text("SELECT id,base_unit_id FROM forge.products"))).one()
        conversion = (
            await db.execute(
                text("SELECT product_id,unit_id,unit_to_base_factor FROM forge.product_units")
            )
        ).one()
        assert (conversion.product_id, conversion.unit_id, conversion.unit_to_base_factor) == (
            product.id,
            product.base_unit_id,
            1,
        )


async def test_independent_worker_restarts_progress_past_embedding_backlog_without_redis(
    catalog_client,
    identities,
    monkeypatch,
):
    c, identity = catalog_client, identities[0]
    product, _, _, _ = await product_fixture(c)
    batch, _, _ = await confirmed(c, [[f"RESTART-{i}", f"恢复品牌{i}", ""] for i in range(5)])
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        backlog = (
            await db.execute(
                text(
                    "SELECT count(*) FROM forge.outbox_events WHERE "
                    "event_type='catalog.products.create' AND processed_at IS NULL"
                )
            )
        ).scalar_one()
        assert backlog == 1
    import redis

    def no_redis(*args, **kwargs):
        raise AssertionError("Import resume must not require Redis or embedding availability")

    monkeypatch.setattr(redis.Redis, "from_url", no_redis)
    first = await worker.run(identity["org"], limit=2)
    assert first["succeeded"] == 2 and first["failed"] == 0
    assert (await detail(c, batch))["pending"] == 3
    second = await worker.run(identity["org"], limit=2)
    assert second["succeeded"] == 2 and second["failed"] == 0
    assert (await detail(c, batch))["pending"] == 1
    assert (await worker.run(identity["org"], limit=2))["succeeded"] == 1
    assert (await detail(c, batch))["status"] == "COMPLETED"
    async with sessions.begin() as db:
        await set_tenant(db, identity["org"])
        assert (
            await db.execute(
                text(
                    "SELECT count(*) FROM forge.outbox_events WHERE "
                    "event_type='catalog.products.create' AND processed_at IS NULL"
                )
            )
        ).scalar_one() == 1
        assert (
            await db.execute(
                text("SELECT count(*) FROM forge.products WHERE id=:id"), {"id": product["id"]}
            )
        ).scalar_one() == 1


async def test_ten_thousand_row_batch_executes_in_bounded_durable_segments(
    catalog_client, identities
):
    c, identity = catalog_client, identities[0]
    batch, _, _ = await confirmed(c, [[f"BULK-{i:05d}", f"批量品牌{i}", ""] for i in range(10_000)])
    current = await detail(c, batch)
    assert current["total"] == current["pending"] == 10_000
    first = await worker.run(identity["org"], limit=2)
    assert first["succeeded"] == 2 and first["failed"] == 0
    current = await detail(c, batch)
    assert current["succeeded"] == 2 and current["pending"] == 9998
    assert len((await facts(identity))["brands"]) == 2
    assert (await worker.run(identity["org"], limit=2))["succeeded"] == 2
    current = await detail(c, batch)
    assert current["succeeded"] == 4 and current["pending"] == 9996
    assert len((await facts(identity))["brands"]) == 4


async def test_cancel_during_business_write_rolls_back_and_new_worker_resumes_once(
    catalog_client,
    identities,
    monkeypatch,
):
    c, identity = catalog_client, identities[0]
    batch, _, _ = await confirmed(c)
    reached, release = asyncio.Event(), asyncio.Event()
    original = runner.write_command

    async def pause(*args, **kwargs):
        result = await original(*args, **kwargs)
        reached.set()
        await release.wait()
        return result

    with monkeypatch.context() as patch:
        patch.setattr(runner, "write_command", pause)
        task = asyncio.create_task(runner.run_one(sessions, identity["org"]))
        try:
            await asyncio.wait_for(reached.wait(), 5)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert await facts(identity) == {"brands": [], "audits": [], "outbox": 0}
    row = (await rows(c, batch))[0]
    assert row["status"] == "PENDING" and row["target_id"] is None and row["attempts"] == 0
    assert (await worker.run(identity["org"], limit=1))["succeeded"] == 1
    assert (await worker.run(identity["org"], limit=1))["succeeded"] == 0
    assert len((await facts(identity))["brands"]) == 1
    row = (await rows(c, batch))[0]
    assert row["status"] == "SUCCEEDED" and row["attempts"] == 1


@pytest.mark.parametrize(
    "resource,permission",
    [
        ("product-prices", "product.price.read"),
        ("customers", "customer.read"),
        ("suppliers", "supplier.read"),
    ],
)
@pytest.mark.parametrize("revoke_import_read", [False, True])
async def test_sensitive_preview_download_and_cached_mutations_recheck_current_read_permissions(
    catalog_client,
    identities,
    monkeypatch,
    resource,
    permission,
    revoke_import_read,
):
    c, identity = catalog_client, identities[0]
    if resource == "product-prices":
        product, _, _, _ = await product_fixture(c)
        headers = ["商品编码", "价格类型", "价格"]
        values, secret = [[product["sku"], "standard", "127.123456"]], "127.123456"
    else:
        headers = ["编码", "名称", "电话"]
        values, secret = [["PRIVATE-PARTY", "测试联系资料", "00123456789"]], "00123456789"
    batch, confirm_body, confirm_key = await confirmed(
        c, values, resource=resource, headers=headers
    )
    assert secret in str(await rows(c, batch))
    assert (await c.get(BASE + "/" + batch["id"] + "/result.xlsx")).status_code == 200

    async def fail(*args, **kwargs):
        raise RuntimeError("temporary failure permits an explicit retry")

    with monkeypatch.context() as patch:
        patch.setattr(runner, "write_command", fail)
        assert await runner.run_one(sessions, identity["org"]) == "FAILED"
    current = await detail(c, batch)
    failed = (await rows(c, batch))[0]
    retry_body = {"expected_version": current["version"], "row_ids": [failed["id"]]}
    retry_key = uuid4().hex
    assert (
        await create(c, "catalog-imports/" + batch["id"] + "/retry", retry_body, retry_key)
    ).status_code == 200
    revoked = "catalog.import.read" if revoke_import_read else permission
    with create_engine(settings().migration_database_url).begin() as db:
        db.execute(
            text(
                "DELETE FROM forge.role_permissions WHERE organization_id=:org "
                "AND permission_code=:permission"
            ),
            identity | {"permission": revoked},
        )
    for suffix in ("", "/rows", "/result.xlsx"):
        response = await c.get(BASE + "/" + batch["id"] + suffix)
        assert response.status_code == 403, response.text
        assert secret not in response.text
    assert (await c.get(BASE + "/templates/" + resource)).status_code == 403
    listed = await c.get(BASE)
    if revoke_import_read:
        assert listed.status_code == 403
    else:
        assert (
            listed.status_code == 200
            and listed.json()["items"] == []
            and listed.json()["total"] == 0
        )
    for action, body, key in (
        ("confirm", confirm_body, confirm_key),
        ("retry", retry_body, retry_key),
    ):
        response = await create(c, "catalog-imports/" + batch["id"] + "/" + action, body, key)
        assert response.status_code == 403 and secret not in response.text
