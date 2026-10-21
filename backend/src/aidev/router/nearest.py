"""Tier 0 nearest neighbour: small talk the rules miss ("what can you do?", "tell me a
joke") is matched against labeled example messages by embedding similarity.

It only ever answers "chitchat", and only when the nearest chit-chat example is both
close and clearly closer than any other intent's. Everything else goes to the
classifier, which also extracts the systems involved. The thresholds are tuned by
leave-one-out over the examples, never on the golden set (ADR 012).
"""

import math
import operator
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from aidev.rag.embeddings import Embedder
from aidev.router.intents import Intent


@dataclass(frozen=True)
class Example:
    text: str
    intent: Intent


@dataclass(frozen=True)
class Thresholds:
    min_similarity: float
    min_margin: float  # over the closest example of any other intent


# Chosen by `python -m aidev.router tune` for voyage-4 (ADR 012). Re-tune when the
# embedding model or the example set changes.
DEFAULT_THRESHOLDS = Thresholds(min_similarity=0.72, min_margin=0.17)


@dataclass(frozen=True)
class Match:
    similarity: float  # to the nearest chit-chat example
    margin: float  # minus the nearest example of another intent
    example: str


def load_examples(path: Path) -> list[Example]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return [Example(e["text"], Intent(e["intent"])) for e in data["example"]]


class ExampleIndex:
    def __init__(self, embedder: Embedder, examples: Sequence[Example]) -> None:
        self.embedder = embedder
        self.examples = list(examples)
        self._vectors: list[list[float]] | None = None

    async def vectors(self) -> list[list[float]]:
        """Embedded once, on first use. Messages and examples are both embedded as
        documents, so the similarity is symmetric."""
        if self._vectors is None:
            raw = await self.embedder.embed_documents([e.text for e in self.examples])
            self._vectors = [_unit(v) for v in raw]
        return self._vectors

    async def embed(self, text: str) -> list[float]:
        (vector,) = await self.embedder.embed_documents([text])
        return _unit(vector)

    async def match(self, vector: Sequence[float], skip: int | None = None) -> Match | None:
        """Nearest chit-chat example and its margin. `skip` leaves one example out."""
        best: dict[bool, tuple[float, int]] = {}
        for i, v in enumerate(await self.vectors()):
            if i == skip:
                continue
            is_chat = self.examples[i].intent is Intent.CHITCHAT
            sim = sum(map(operator.mul, vector, v))
            if sim > best.get(is_chat, (-2.0, -1))[0]:
                best[is_chat] = (sim, i)
        if True not in best:
            return None
        sim, i = best[True]
        other = best.get(False, (-1.0, -1))[0]
        return Match(sim, sim - other, self.examples[i].text)


def is_chitchat(match: Match | None, thresholds: Thresholds) -> bool:
    return (
        match is not None
        and match.similarity >= thresholds.min_similarity
        and match.margin >= thresholds.min_margin
    )


def _unit(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]
