from pathlib import Path

import pytest

from aidev.config import get_settings
from aidev.rag.eval import (
    CONFIGS,
    CachedQueryEmbedder,
    CaseResult,
    ConfigReport,
    EvalCase,
    format_report,
    load_cases,
)
from aidev.rag.search import rrf
from tests.rag_fakes import FakeEmbedder


def test_rrf_rewards_agreement_between_rankings() -> None:
    # 3 is mid-ranked by both lists; it should beat items only one list likes.
    fused = rrf([[1, 3, 4], [2, 3, 5]])

    assert fused[0] == 3
    assert set(fused) == {1, 2, 3, 4, 5}


def test_rrf_handles_an_empty_ranking() -> None:
    assert rrf([[7, 8], []]) == [7, 8]


def test_report_metrics() -> None:
    report = ConfigReport(CONFIGS["bm25"], k=8)
    report.results = [
        CaseResult(EvalCase("a", ("x", "y")), ["x", "q", "y"]),  # hit at 1, recall 1
        CaseResult(EvalCase("b", ("x", "y")), ["q", "y"]),  # hit at 2, recall 0.5
        CaseResult(EvalCase("c", ("x",)), ["q"]),  # miss
    ]
    report.latencies_ms = [5.0, 7.0, 9.0]

    assert report.hit_rate == pytest.approx(2 / 3)
    assert report.recall == pytest.approx(0.5)
    assert report.mrr == pytest.approx((1 + 0.5 + 0) / 3)
    assert report.p50_ms == 7.0
    assert [m.case.query for m in report.misses()] == ["c"]
    assert "| bm25 | 0.67 | 0.50 | 0.50 | 7 |" in format_report([report], 3)


def test_golden_set_is_well_formed() -> None:
    cases = load_cases(get_settings().corpus_dir / "eval" / "retrieval.toml")

    assert len(cases) >= 40
    assert len({c.query for c in cases}) == len(cases)
    for case in cases:
        assert case.relevant, case.query
        assert all(key.count("/") >= 2 for key in case.relevant), case.query


async def test_query_cache_avoids_repeat_calls(tmp_path: Path) -> None:
    inner = FakeEmbedder()
    path = tmp_path / "cache.json"

    first = await CachedQueryEmbedder(inner, path).embed_query("hello")
    again = await CachedQueryEmbedder(inner, path).embed_query("hello")  # fresh instance

    assert first == again
    assert inner.queries == ["hello"]
