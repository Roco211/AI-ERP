"""A backlog requiring the optional model cannot stall ordinary ERP events."""

from uuid import uuid4

import pytest
from sqlalchemy import text

from forge_erp.core.config import settings
from forge_erp.core.db import sessions, set_tenant
from forge_erp.modules.catalog.infrastructure.embeddings import EmbeddingUnavailable
from forge_erp.workers import outbox


@pytest.mark.parametrize("model_enabled", [False, True])
async def test_ordinary_events_advance_past_large_model_backlog(
    identities, monkeypatch, model_enabled
):
    org = identities[0]["org"]
    monkeypatch.setattr(settings(), "embedding_enabled", model_enabled)
    calls = []

    async def unavailable(*args):
        calls.append(args)
        raise EmbeddingUnavailable("test-only unavailable model")

    monkeypatch.setattr(outbox, "index_product", unavailable)
    async with sessions.begin() as db:
        await set_tenant(db, org)
        await db.execute(
            text(
                "INSERT INTO forge.outbox_events "
                "(organization_id,event_type,request_id,payload,created_at) "
                "SELECT :org,'catalog.products.create','backlog-test',"
                "jsonb_build_object('resource_id',CAST(:product AS text)),"
                "now()-interval '1 day' FROM generate_series(1,101)"
            ),
            {"org": org, "product": str(uuid4())},
        )
        await db.execute(
            text(
                "INSERT INTO forge.outbox_events "
                "(organization_id,event_type,request_id,payload) "
                "VALUES(:org,'identity.session.created','ordinary-event','{}'::jsonb)"
            ),
            {"org": org},
        )
    assert await outbox.drain_outbox(org) == 1
    assert len(calls) == (1 if model_enabled else 0)
    async with sessions.begin() as db:
        await set_tenant(db, org)
        assert (
            await db.execute(
                text("SELECT count(*) FROM forge.outbox_events WHERE processed_at IS NULL")
            )
        ).scalar_one() == 101
        assert (
            await db.execute(
                text("SELECT attempts FROM forge.outbox_events WHERE request_id='ordinary-event'")
            )
        ).scalar_one() == 1
    assert await outbox.drain_outbox(org) == 0
