from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Table, delete, func, insert, literal, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from aidev.catalog.models import Operation, ParsedSpec, SchemaDef, WebhookEvent
from aidev.catalog.openapi import path_regex
from aidev.catalog.tables import api_operations, api_schemas, api_specs, api_webhook_events

_INSERT_CHUNK = 500


@dataclass(frozen=True)
class SpecSummary:
    provider: str
    name: str
    api_version: str
    operations: int
    webhook_events: int
    schemas: int


@dataclass(frozen=True)
class StoredSpec:
    provider: str
    name: str
    spec: ParsedSpec


class CatalogStore:
    """Read/write access to the API catalog. Lookups are exact: they answer whether an
    operation, event or schema exists, never what is "similar" (that's the docs index)."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    # -- writes -----------------------------------------------------------------------

    async def has_spec(self, provider: str, name: str, content_hash: str) -> bool:
        query = select(literal(True)).where(
            api_specs.c.provider == provider,
            api_specs.c.name == name,
            api_specs.c.content_hash == content_hash,
        )
        async with self.engine.connect() as conn:
            return bool(await conn.scalar(query))

    async def replace_spec(
        self, provider: str, name: str, source_url: str, content_hash: str, spec: ParsedSpec
    ) -> None:
        """Atomically swap a spec document's rows for a newly parsed version."""
        async with self.engine.begin() as conn:
            await conn.execute(
                delete(api_specs).where(api_specs.c.provider == provider, api_specs.c.name == name)
            )
            spec_id = await conn.scalar(
                insert(api_specs)
                .values(
                    provider=provider,
                    name=name,
                    source_url=source_url,
                    content_hash=content_hash,
                    title=spec.title,
                    api_version=spec.api_version,
                    spec_format=spec.spec_format,
                    base_urls=spec.base_urls,
                )
                .returning(api_specs.c.id)
            )
            common = {"spec_id": spec_id, "provider": provider}
            await _insert_many(
                conn,
                api_operations,
                (
                    {
                        **common,
                        "op_id": op.op_id,
                        "method": op.method,
                        "path": op.path,
                        "path_regex": path_regex(op.path),
                        "path_param_count": op.path.count("{"),
                        "deprecated": op.deprecated,
                        "data": op.model_dump(mode="json"),
                    }
                    for op in spec.operations
                ),
            )
            await _insert_many(
                conn,
                api_webhook_events,
                (
                    {**common, "name": e.name, "data": e.model_dump(mode="json")}
                    for e in spec.webhook_events
                ),
            )
            await _insert_many(
                conn,
                api_schemas,
                (
                    {**common, "name": s.name, "data": s.model_dump(mode="json")}
                    for s in spec.schemas
                ),
            )

    async def delete_provider(self, provider: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(delete(api_specs).where(api_specs.c.provider == provider))

    # -- lookups ----------------------------------------------------------------------

    async def get_operation(self, provider: str, op_id: str) -> Operation | None:
        query = select(api_operations.c.data).where(
            api_operations.c.provider == provider, api_operations.c.op_id == op_id
        )
        data = await self._first(query)
        return Operation.model_validate(data) if data else None

    async def match_operation(self, provider: str, method: str, path: str) -> Operation | None:
        """The operation a concrete request (`POST /v1/customers/cus_123`) would hit.
        `path` is relative to the API base URL. When several templates match, the one
        with the fewest parameters wins: `/repos/o/r/issues/comments` is
        `/repos/{owner}/{repo}/issues/comments`, not `.../issues/{issue_number}`."""
        query = (
            select(api_operations.c.data)
            .where(
                api_operations.c.provider == provider,
                api_operations.c.method == method.upper(),
                literal(path).regexp_match(api_operations.c.path_regex),
            )
            .order_by(api_operations.c.path_param_count, func.length(api_operations.c.path).desc())
        )
        data = await self._first(query)
        return Operation.model_validate(data) if data else None

    async def get_webhook_event(self, provider: str, name: str) -> WebhookEvent | None:
        query = select(api_webhook_events.c.data).where(
            api_webhook_events.c.provider == provider, api_webhook_events.c.name == name
        )
        data = await self._first(query)
        return WebhookEvent.model_validate(data) if data else None

    async def get_schema(self, provider: str, name: str) -> SchemaDef | None:
        query = select(api_schemas.c.data).where(
            api_schemas.c.provider == provider, api_schemas.c.name == name
        )
        data = await self._first(query)
        return SchemaDef.model_validate(data) if data else None

    async def summary(self) -> list[SpecSummary]:
        def count(table: Table) -> Any:
            return select(func.count()).where(table.c.spec_id == api_specs.c.id).scalar_subquery()

        query = select(
            api_specs.c.provider,
            api_specs.c.name,
            api_specs.c.api_version,
            count(api_operations),
            count(api_webhook_events),
            count(api_schemas),
        ).order_by(api_specs.c.provider, api_specs.c.name)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).all()
        return [SpecSummary(*row) for row in rows]

    async def load_specs(self) -> list[StoredSpec]:
        """Every ingested spec document with its full contents (for building the docs
        index; lookups should use the targeted methods above)."""
        async with self.engine.connect() as conn:
            specs = (await conn.execute(select(api_specs).order_by(api_specs.c.id))).all()
            children: dict[str, dict[int, list[Any]]] = {}
            for name, table in (
                ("operations", api_operations),
                ("webhook_events", api_webhook_events),
                ("schemas", api_schemas),
            ):
                rows = await conn.execute(
                    select(table.c.spec_id, table.c.data).order_by(table.c.id)
                )
                by_spec: dict[int, list[Any]] = {}
                for spec_id, data in rows:
                    by_spec.setdefault(spec_id, []).append(data)
                children[name] = by_spec
        return [
            StoredSpec(
                provider=s.provider,
                name=s.name,
                spec=ParsedSpec(
                    title=s.title,
                    api_version=s.api_version,
                    spec_format=s.spec_format,
                    base_urls=s.base_urls,
                    operations=children["operations"].get(s.id, []),
                    webhook_events=children["webhook_events"].get(s.id, []),
                    schemas=children["schemas"].get(s.id, []),
                ),
            )
            for s in specs
        ]

    async def _first(self, query: Any) -> Any:
        async with self.engine.connect() as conn:
            return (await conn.execute(query.limit(1))).scalar()


async def _insert_many(conn: AsyncConnection, table: Table, rows: Iterable[dict[str, Any]]) -> None:
    batch: list[dict[str, Any]] = []
    for row in rows:
        batch.append(row)
        if len(batch) == _INSERT_CHUNK:
            await conn.execute(insert(table), batch)
            batch = []
    if batch:
        await conn.execute(insert(table), batch)
