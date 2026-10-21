"""Write the corpus into `doc_chunks` and embed it.

Indexing is two phases, so it survives tight embedding rate limits:
1. `sync` writes chunks without embeddings. It's fast and local, and full-text search
   works as soon as it finishes. Unchanged documents are left alone, and a changed
   chunk whose exact text was embedded before gets that vector back.
2. `embed_pending` embeds whatever has no vector yet, committing batch by batch, so an
   interrupted run continues where it stopped.
"""

import hashlib
import logging
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, bindparam, delete, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from aidev.rag.chunking import Chunk
from aidev.rag.documents import SourceDoc
from aidev.rag.embeddings import Embedder
from aidev.rag.tables import doc_chunks

log = logging.getLogger(__name__)

_WRITE_BATCH = 500


def embedding_text(breadcrumb: str, content: str) -> str:
    """What gets embedded and reranked: the breadcrumb gives the chunk its context."""
    return f"{breadcrumb}\n\n{content}"


def content_hash(chunk: Chunk) -> str:
    return hashlib.sha256(embedding_text(chunk.breadcrumb, chunk.text).encode()).hexdigest()


@dataclass(frozen=True)
class SyncStats:
    documents: int
    chunks: int
    unchanged: int
    written: int  # new or changed documents
    removed: int
    reused_embeddings: int


@dataclass(frozen=True)
class IndexStatus:
    provider: str
    doc_type: str
    documents: int
    chunks: int
    embedded: int


class DocIndex:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def sync(
        self,
        docs: Sequence[SourceDoc],
        embedding_model: str,
        scope: Collection[str] | None = None,
    ) -> SyncStats:
        """Make the index match `docs`: the corpus is the source of truth. Documents
        missing from `docs` are removed, but only for providers in `scope`; `None`
        means the whole index (what a full rebuild wants)."""
        keys = [d.key for d in docs]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate document keys in corpus")
        if scope is not None and (outside := {d.provider for d in docs} - set(scope)):
            raise ValueError(f"documents outside the sync scope: {sorted(outside)}")

        async with self.engine.begin() as conn:
            current = select(
                doc_chunks.c.doc_key,
                doc_chunks.c.content_hash,
                doc_chunks.c.title,
                doc_chunks.c.url,
            ).order_by(doc_chunks.c.doc_key, doc_chunks.c.chunk_index)
            if scope is not None:
                current = current.where(doc_chunks.c.provider.in_(scope))
            rows = await conn.execute(current)
            existing: dict[str, list[tuple[str, str, str | None]]] = {}
            for key, digest, title, url in rows:
                existing.setdefault(key, []).append((digest, title, url))

            changed = [
                d
                for d in docs
                if existing.get(d.key) != [(content_hash(c), d.title, d.url) for c in d.chunks]
            ]
            stale = sorted(set(existing) - set(keys))

            wanted = {content_hash(c) for d in changed for c in d.chunks}
            reusable: dict[str, Any] = {}
            if wanted:
                found = await conn.execute(
                    select(doc_chunks.c.content_hash, doc_chunks.c.embedding).where(
                        doc_chunks.c.content_hash.in_(wanted),
                        doc_chunks.c.embedding_model == embedding_model,
                        doc_chunks.c.embedding.is_not(None),
                    )
                )
                reusable = {digest: vector for digest, vector in found}

            doomed = stale + [d.key for d in changed]
            for i in range(0, len(doomed), _WRITE_BATCH):
                await conn.execute(
                    delete(doc_chunks).where(doc_chunks.c.doc_key.in_(doomed[i : i + _WRITE_BATCH]))
                )

            new_rows = []
            reused = 0
            for d in changed:
                for i, chunk in enumerate(d.chunks):
                    digest = content_hash(chunk)
                    vector = reusable.get(digest)
                    reused += vector is not None
                    new_rows.append(
                        {
                            "doc_key": d.key,
                            "chunk_index": i,
                            "provider": d.provider,
                            "doc_type": d.doc_type,
                            "title": d.title,
                            "url": d.url,
                            "breadcrumb": chunk.breadcrumb,
                            "content": chunk.text,
                            "content_hash": digest,
                            "embedding": vector,
                            "embedding_model": embedding_model if vector is not None else None,
                        }
                    )
            for i in range(0, len(new_rows), _WRITE_BATCH):
                await conn.execute(insert(doc_chunks), new_rows[i : i + _WRITE_BATCH])

        return SyncStats(
            documents=len(docs),
            chunks=sum(len(d.chunks) for d in docs),
            unchanged=len(docs) - len(changed),
            written=len(changed),
            removed=len(stale),
            reused_embeddings=reused,
        )

    async def embed_pending(
        self,
        embedder: Embedder,
        batch_size: int = 64,
        progress: Callable[[int, int], None] | None = None,
        scope: Collection[str] | None = None,
    ) -> int:
        """Embed chunks that have no vector from the current model, limited to providers
        in `scope` (`None` means all). Each batch commits on its own, so stopping halfway
        loses at most one batch of work."""
        pending = or_(
            doc_chunks.c.embedding.is_(None), doc_chunks.c.embedding_model != embedder.model
        )
        if scope is not None:
            pending = and_(pending, doc_chunks.c.provider.in_(scope))
        async with self.engine.connect() as conn:
            total = await conn.scalar(select(func.count()).where(pending)) or 0
        done = 0
        while True:
            async with self.engine.connect() as conn:
                rows = (
                    await conn.execute(
                        select(doc_chunks.c.id, doc_chunks.c.breadcrumb, doc_chunks.c.content)
                        .where(pending)
                        .order_by(doc_chunks.c.id)
                        .limit(batch_size)
                    )
                ).all()
            if not rows:
                return done
            vectors = await embedder.embed_documents([embedding_text(b, c) for _, b, c in rows])
            async with self.engine.begin() as conn:
                await conn.execute(
                    update(doc_chunks)
                    .where(doc_chunks.c.id == bindparam("chunk_id"))
                    .values(
                        embedding=bindparam("vector"),
                        embedding_model=embedder.model,
                        updated_at=func.now(),
                    ),
                    [{"chunk_id": r.id, "vector": v} for r, v in zip(rows, vectors, strict=True)],
                )
            done += len(rows)
            if progress:
                progress(done, total)

    async def status(self) -> list[IndexStatus]:
        query = (
            select(
                doc_chunks.c.provider,
                doc_chunks.c.doc_type,
                func.count(func.distinct(doc_chunks.c.doc_key)),
                func.count(),
                func.count(doc_chunks.c.embedding),
            )
            .group_by(doc_chunks.c.provider, doc_chunks.c.doc_type)
            .order_by(doc_chunks.c.provider, doc_chunks.c.doc_type)
        )
        async with self.engine.connect() as conn:
            return [IndexStatus(*row) for row in await conn.execute(query)]
