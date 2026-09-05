"""LangGraph persistence over short, owner-scoped PostgreSQL transactions.

Only the asynchronous saver API and a single root graph are supported. A saver
is constructed for an authenticated turn; runnable configuration never chooses
its organization, owner, conversation or turn. No setup/DDL or admin connection.
"""

import json
from collections.abc import AsyncIterator, Collection, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forge_erp.core.context import RuntimeContext
from forge_erp.core.errors import Problem
from forge_erp.modules.assistant.domain.checkpoint import (
    MAX_CHECKPOINT_BYTES,
    MAX_METADATA_BYTES,
    CheckpointJSONCodec,
)

_SCOPE = (
    "organization_id=:org AND owner_id=:owner AND conversation_id=:conversation AND turn_id=:turn"
)
_MAX_CHECKPOINTS = 128
_MAX_WRITES = 2048


class PostgreSQLCheckpointSaver(BaseCheckpointSaver[int]):
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        context: RuntimeContext,
        conversation_id: UUID,
        turn_id: UUID,
        attempt: int | None = None,
    ) -> None:
        super().__init__(serde=CheckpointJSONCodec())
        self._sessions = sessions
        self._context = context
        self._conversation_id = conversation_id
        self._turn_id = turn_id
        if attempt is not None and (type(attempt) is not int or attempt < 0):
            raise ValueError("Invalid checkpoint execution attempt")
        self._attempt = attempt

    def with_allowlist(
        self, extra_allowlist: Collection[tuple[str, ...]]
    ) -> PostgreSQLCheckpointSaver:
        # LangGraph probes this API during compilation. The closed JSON codec
        # intentionally cannot gain dynamic classes from graph schema hints.
        return self

    def config(self, checkpoint_id: str | None = None) -> RunnableConfig:
        configurable: dict[str, Any] = {
            "thread_id": str(self._turn_id),
            "checkpoint_ns": "",
        }
        if checkpoint_id is not None:
            configurable["checkpoint_id"] = checkpoint_id
        return {"configurable": configurable}

    def _params(self) -> dict[str, Any]:
        return {
            "org": self._context.organization_id,
            "owner": self._context.user_id,
            "conversation": self._conversation_id,
            "turn": self._turn_id,
        }

    def _check_config(self, config: RunnableConfig) -> str | None:
        configurable = config.get("configurable", {})
        if (
            configurable.get("thread_id") != str(self._turn_id)
            or configurable.get("checkpoint_ns", "") != ""
        ):
            raise ValueError("Checkpoint configuration does not match the bound turn")
        checkpoint_id = configurable.get("checkpoint_id")
        if checkpoint_id is not None and (
            type(checkpoint_id) is not str
            or not 1 <= len(checkpoint_id) <= 128
            or "\x00" in checkpoint_id
        ):
            raise ValueError("Invalid checkpoint ID")
        return checkpoint_id

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncSession]:
        async with self._sessions.begin() as db:
            await db.execute(
                text(
                    "SELECT set_config('app.organization_id',:org,true),"
                    "set_config('app.user_id',:owner,true)"
                ),
                {
                    "org": str(self._context.organization_id),
                    "owner": str(self._context.user_id),
                },
            )
            yield db

    async def _lock_turn(self, db: AsyncSession) -> None:
        # LangGraph can persist independent task writes concurrently. Serialize
        # their size/count checks across saver instances without holding any lock
        # between calls. A hash collision merely serializes unrelated progress.
        key = ":".join(str(value) for value in self._params().values())
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": "assistant-checkpoint:" + key},
        )

    async def _active(self, db: AsyncSession, *, writing: bool = False) -> bool:
        if writing:
            await self._lock_turn(db)
            # Lock parent before child, matching review/delete and retention.
            # A joined FOR SHARE OF c,t can lock t first despite clause order.
            parent = await db.execute(
                text(
                    "SELECT 1 FROM forge.assistant_conversations WHERE organization_id=:org "
                    "AND owner_id=:owner AND id=:conversation AND deleted_at IS NULL "
                    "AND expires_at>clock_timestamp() FOR SHARE"
                ),
                self._params(),
            )
            if parent.scalar_one_or_none() is None:
                raise Problem(
                    404, "AI_STATE_UNAVAILABLE", "AI conversation is unavailable or expired"
                )
        # FOR SHARE serializes with deletion/expiry cleanup, only for this short
        # persistence operation. No model or tool execution occurs under it.
        row = await db.execute(
            text(
                "SELECT 1 FROM forge.assistant_turns t "
                "JOIN forge.assistant_conversations c ON c.organization_id=t.organization_id "
                "AND c.owner_id=t.owner_id AND c.id=t.conversation_id "
                "WHERE t.organization_id=:org AND t.owner_id=:owner "
                "AND t.conversation_id=:conversation AND t.id=:turn "
                "AND c.deleted_at IS NULL AND c.expires_at>clock_timestamp() "
                "AND t.expires_at>clock_timestamp()"
                + (" AND t.attempts=:attempt" if self._attempt is not None else "")
                + (" FOR SHARE OF t" if writing else "")
            ),
            {**self._params(), "attempt": self._attempt},
        )
        active = row.scalar_one_or_none() is not None
        if writing and not active:
            raise Problem(404, "AI_STATE_UNAVAILABLE", "AI conversation is unavailable or expired")
        return active

    def _json(self, value: Any) -> str:
        return self.serde.dumps_typed(value)[1].decode("utf-8")

    def _decode(self, value: Any) -> Any:
        return self.serde.loads_typed(
            ("forge-json-v1", json.dumps(value, ensure_ascii=False).encode("utf-8"))
        )

    async def _tuple(self, db: AsyncSession, row: RowMapping) -> CheckpointTuple:
        params = {**self._params(), "checkpoint_id": row["checkpoint_id"]}
        writes = (
            (
                await db.execute(
                    text(
                        "SELECT task_id,channel,value FROM forge.assistant_checkpoint_writes WHERE "
                        + _SCOPE
                        + " AND checkpoint_ns='' AND checkpoint_id=:checkpoint_id "
                        "ORDER BY task_id,write_idx LIMIT 2049"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        if len(writes) > _MAX_WRITES:
            raise ValueError("Checkpoint write limit exceeded")
        return CheckpointTuple(
            config=self.config(row["checkpoint_id"]),
            checkpoint=cast(Checkpoint, self._decode(row["checkpoint"])),
            metadata=cast(CheckpointMetadata, row["metadata"]),
            parent_config=(
                self.config(row["parent_checkpoint_id"]) if row["parent_checkpoint_id"] else None
            ),
            pending_writes=[
                (write["task_id"], write["channel"], self._decode(write["value"]))
                for write in writes
            ],
        )

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        checkpoint_id = self._check_config(config)
        params = {**self._params(), "checkpoint_id": checkpoint_id}
        async with self._transaction() as db:
            if not await self._active(db):
                return None
            row = (
                (
                    await db.execute(
                        text(
                            "SELECT checkpoint_id,parent_checkpoint_id,checkpoint,metadata "
                            "FROM forge.assistant_checkpoints WHERE "
                            + _SCOPE
                            + " AND checkpoint_ns='' "
                            + ("AND checkpoint_id=:checkpoint_id " if checkpoint_id else "")
                            + "ORDER BY checkpoint_id DESC LIMIT 1"
                        ),
                        params,
                    )
                )
                .mappings()
                .one_or_none()
            )
            return await self._tuple(db, row) if row is not None else None

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        checkpoint_id = self._check_config(config or self.config())
        before_id = self._check_config(before) if before is not None else None
        if limit is not None and (type(limit) is not int or not 0 <= limit <= 100):
            raise ValueError("Checkpoint history limit must be between 0 and 100")
        if limit == 0:
            return
        # Metadata deliberately excludes RunnableConfig, credentials and arbitrary
        # config values. Filter only this same small framework metadata vocabulary.
        if filter and set(filter) - {"source", "step", "parents", "run_id"}:
            raise ValueError("Unsupported checkpoint metadata filter")
        metadata_filter = self._metadata(filter or {})
        params = {
            **self._params(),
            "checkpoint_id": checkpoint_id,
            "before": before_id,
            "metadata": metadata_filter,
            "limit": limit if limit is not None else 25,
        }
        async with self._transaction() as db:
            if not await self._active(db):
                return
            rows = (
                (
                    await db.execute(
                        text(
                            "SELECT checkpoint_id,parent_checkpoint_id,checkpoint,metadata "
                            "FROM forge.assistant_checkpoints WHERE "
                            + _SCOPE
                            + " AND checkpoint_ns='' AND metadata @> CAST(:metadata AS jsonb) "
                            + ("AND checkpoint_id=:checkpoint_id " if checkpoint_id else "")
                            + ("AND checkpoint_id<:before " if before_id else "")
                            + "ORDER BY checkpoint_id DESC LIMIT :limit"
                        ),
                        params,
                    )
                )
                .mappings()
                .all()
            )
            results = [await self._tuple(db, row) for row in rows]
        # Never retain a DB transaction while the consumer is iterating/working.
        for result in results:
            yield result

    def _metadata(self, metadata: Mapping[str, Any]) -> str:
        permitted = {"source", "step", "parents", "run_id"}
        clean = {key: value for key, value in metadata.items() if key in permitted}
        # Validate with the same closed type policy, while keeping ordinary JSONB
        # for metadata containment queries. No implicit RunnableConfig metadata.
        self._json(clean)
        data = json.dumps(clean, ensure_ascii=False, allow_nan=False)
        if len(data.encode("utf-8")) > MAX_METADATA_BYTES:
            raise ValueError("Checkpoint metadata exceeds the size limit")
        return data

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        parent_id = self._check_config(config)
        checkpoint_id = checkpoint["id"]
        self._check_config(self.config(checkpoint_id))
        params = {
            **self._params(),
            "checkpoint_id": checkpoint_id,
            "parent_id": parent_id,
            "checkpoint": self._json(checkpoint),
            "metadata": self._metadata(metadata),
        }
        async with self._transaction() as db:
            await self._active(db, writing=True)
            count = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM forge.assistant_checkpoints WHERE "
                        + _SCOPE
                        + " AND checkpoint_id<>:checkpoint_id"
                    ),
                    params,
                )
            ).scalar_one()
            if count >= _MAX_CHECKPOINTS:
                raise ValueError("Checkpoint count limit exceeded")
            await db.execute(
                text(
                    "INSERT INTO forge.assistant_checkpoints "
                    "(organization_id,owner_id,conversation_id,turn_id,checkpoint_id,"
                    "parent_checkpoint_id,checkpoint,metadata) VALUES "
                    "(:org,:owner,:conversation,:turn,:checkpoint_id,:parent_id,"
                    "CAST(:checkpoint AS jsonb),CAST(:metadata AS jsonb)) "
                    "ON CONFLICT (organization_id,owner_id,conversation_id,turn_id,"
                    "checkpoint_ns,checkpoint_id) DO UPDATE SET "
                    "checkpoint=EXCLUDED.checkpoint,metadata=EXCLUDED.metadata"
                ),
                params,
            )
        return self.config(checkpoint_id)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        checkpoint_id = self._check_config(config)
        if checkpoint_id is None:
            raise ValueError("Pending writes require a checkpoint ID")
        if (
            not 1 <= len(task_id) <= 128
            or len(task_path) > 1024
            or len(writes) > 128
            or "\x00" in task_id
            or "\x00" in task_path
        ):
            raise ValueError("Invalid or excessive checkpoint pending writes")
        values = []
        for index, (channel, value) in enumerate(writes):
            if not 1 <= len(channel) <= 256 or "\x00" in channel:
                raise ValueError("Invalid checkpoint write channel")
            values.append(
                {
                    **self._params(),
                    "checkpoint_id": checkpoint_id,
                    "task_id": task_id,
                    "task_path": task_path,
                    "write_idx": WRITES_IDX_MAP.get(channel, index),
                    "channel": channel,
                    "value": self._json(value),
                }
            )
        if not values:
            return
        if sum(len(item["value"].encode("utf-8")) for item in values) > MAX_CHECKPOINT_BYTES:
            raise ValueError("Checkpoint pending write batch exceeds the size limit")
        async with self._transaction() as db:
            await self._active(db, writing=True)
            count = (
                await db.execute(
                    text("SELECT count(*) FROM forge.assistant_checkpoint_writes WHERE " + _SCOPE),
                    self._params(),
                )
            ).scalar_one()
            existing = {
                row[0]
                for row in (
                    await db.execute(
                        text(
                            "SELECT write_idx FROM forge.assistant_checkpoint_writes WHERE "
                            + _SCOPE
                            + " AND checkpoint_ns='' AND checkpoint_id=:checkpoint_id "
                            "AND task_id=:task_id"
                        ),
                        {**self._params(), "checkpoint_id": checkpoint_id, "task_id": task_id},
                    )
                ).all()
            }
            new_indexes = {item["write_idx"] for item in values} - existing
            if count + len(new_indexes) > _MAX_WRITES:
                raise ValueError("Checkpoint write count limit exceeded")
            for params in values:
                # LangGraph reserves negative indexes for errors/interrupt/resume;
                # these replace retries. Successful normal writes are first-wins.
                conflict = (
                    "DO UPDATE SET channel=EXCLUDED.channel,value=EXCLUDED.value,"
                    "task_path=EXCLUDED.task_path"
                    if params["write_idx"] < 0
                    else "DO NOTHING"
                )
                await db.execute(
                    text(
                        "INSERT INTO forge.assistant_checkpoint_writes "
                        "(organization_id,owner_id,conversation_id,turn_id,checkpoint_id,"
                        "task_id,task_path,write_idx,channel,value) VALUES "
                        "(:org,:owner,:conversation,:turn,:checkpoint_id,:task_id,:task_path,"
                        ":write_idx,:channel,CAST(:value AS jsonb)) "
                        "ON CONFLICT (organization_id,owner_id,conversation_id,turn_id,"
                        "checkpoint_ns,checkpoint_id,task_id,write_idx) " + conflict
                    ),
                    params,
                )

    async def adelete_thread(self, thread_id: str) -> None:
        self._check_config({"configurable": {"thread_id": thread_id}})
        async with self._transaction() as db:
            await self._lock_turn(db)
            if self._attempt is not None:
                await self._active(db, writing=True)
            for table in ("assistant_checkpoint_writes", "assistant_checkpoints"):
                await db.execute(text(f"DELETE FROM forge.{table} WHERE " + _SCOPE), self._params())
