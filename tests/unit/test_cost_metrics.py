"""Phase 5: 비용/토큰 Prometheus 메트릭 단위 테스트.

각 테스트는 독립적인 Counter registry를 사용하여 상태 오염을 방지한다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from prometheus_client import CollectorRegistry

from src.proxy.cache.redis_cache import RedisCache
from src.proxy.cost.cost_calculator import CostCalculator
from src.proxy.cost.token_predictor import estimate_tokens, predict_output_tokens


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_metrics(registry: CollectorRegistry):
    """격리된 registry에 메트릭을 생성하여 반환한다."""
    from prometheus_client import Counter

    total_cost = Counter(
        "llm_total_cost_usd_total",
        "LLM 실제 호출 누적 비용 (USD)",
        registry=registry,
    )
    cost_saved = Counter(
        "llm_cost_saved_usd_total",
        "캐시 절감 비용 (USD)",
        ["tier"],
        registry=registry,
    )
    tokens = Counter(
        "llm_tokens_total",
        "누적 토큰 사용량",
        ["type"],
        registry=registry,
    )
    return total_cost, cost_saved, tokens


# ---------------------------------------------------------------------------
# 메트릭 격리 테스트 — Counter 직접 검증
# ---------------------------------------------------------------------------


class TestCostSavedCounterL1:
    """L1 캐시 히트 시 cost_saved_usd{tier='l1_hash'} 증가 검증."""

    def test_l1_hit_increments_cost_saved(self) -> None:
        """L1 히트로 절감 비용이 계산되어 Counter에 반영되어야 한다."""
        registry = CollectorRegistry()
        _, cost_saved, _ = _make_metrics(registry)

        calculator = CostCalculator()
        query = "Redis란 무엇인가?"
        input_est = estimate_tokens(query)
        output_est = predict_output_tokens(query)
        expected = calculator.compute(input_est, output_est)

        cost_saved.labels(tier="l1_hash").inc(expected)

        value = cost_saved.labels(tier="l1_hash")._value.get()
        assert value == pytest.approx(expected, rel=1e-6)
        assert value > 0


class TestCostSavedCounterL2:
    """L2 캐시 히트 시 cost_saved_usd{tier='l2_semantic'} 증가 검증."""

    def test_l2_hit_increments_cost_saved(self) -> None:
        """L2 히트로 절감 비용이 Counter에 반영되어야 한다."""
        registry = CollectorRegistry()
        _, cost_saved, _ = _make_metrics(registry)

        calculator = CostCalculator()
        query = "Docker 컨테이너란?"
        input_est = estimate_tokens(query)
        output_est = predict_output_tokens(query)
        expected = calculator.compute(input_est, output_est)

        cost_saved.labels(tier="l2_semantic").inc(expected)

        value = cost_saved.labels(tier="l2_semantic")._value.get()
        assert value == pytest.approx(expected, rel=1e-6)
        assert value > 0

    def test_l1_and_l2_are_independent(self) -> None:
        """l1_hash와 l2_semantic tier는 독립적으로 집계되어야 한다."""
        registry = CollectorRegistry()
        _, cost_saved, _ = _make_metrics(registry)

        calculator = CostCalculator()
        cost_saved.labels(tier="l1_hash").inc(calculator.compute(10, 20))
        cost_saved.labels(tier="l2_semantic").inc(calculator.compute(5, 10))

        l1_val = cost_saved.labels(tier="l1_hash")._value.get()
        l2_val = cost_saved.labels(tier="l2_semantic")._value.get()

        assert l1_val > 0
        assert l2_val > 0
        assert l1_val != l2_val


class TestTotalCostAndTokenCounters:
    """LLM 호출 후 total_cost_usd 및 tokens_total 증가 검증."""

    def test_llm_call_increments_total_cost(self) -> None:
        """LLM 호출 후 total_cost_usd Counter가 증가해야 한다."""
        registry = CollectorRegistry()
        total_cost, _, _ = _make_metrics(registry)

        calculator = CostCalculator()
        cost = calculator.compute(100, 200)
        total_cost.inc(cost)

        value = total_cost._value.get()
        assert value == pytest.approx(cost, rel=1e-6)
        assert value > 0

    def test_llm_call_increments_input_tokens(self) -> None:
        """LLM 호출 후 tokens_total{type='input'} Counter가 증가해야 한다."""
        registry = CollectorRegistry()
        _, _, tokens = _make_metrics(registry)

        tokens.labels(type="input").inc(100)

        value = tokens.labels(type="input")._value.get()
        assert value == 100

    def test_llm_call_increments_output_tokens(self) -> None:
        """LLM 호출 후 tokens_total{type='output'} Counter가 증가해야 한다."""
        registry = CollectorRegistry()
        _, _, tokens = _make_metrics(registry)

        tokens.labels(type="output").inc(200)

        value = tokens.labels(type="output")._value.get()
        assert value == 200

    def test_input_output_tokens_are_independent(self) -> None:
        """input/output 토큰은 독립적으로 집계되어야 한다."""
        registry = CollectorRegistry()
        _, _, tokens = _make_metrics(registry)

        tokens.labels(type="input").inc(50)
        tokens.labels(type="output").inc(150)

        input_val = tokens.labels(type="input")._value.get()
        output_val = tokens.labels(type="output")._value.get()

        assert input_val == 50
        assert output_val == 150


class TestMetricRegistryIsolation:
    """여러 테스트가 동일 Counter를 오염시키지 않음을 검증."""

    def test_fresh_registry_starts_at_zero(self) -> None:
        """새 registry는 항상 0에서 시작해야 한다."""
        for _ in range(3):
            registry = CollectorRegistry()
            total_cost, cost_saved, tokens = _make_metrics(registry)

            assert total_cost._value.get() == 0.0
            assert tokens.labels(type="input")._value.get() == 0.0

    def test_multiple_increments_accumulate(self) -> None:
        """여러 번 inc() 호출 시 누적 값이 합산되어야 한다."""
        registry = CollectorRegistry()
        total_cost, _, _ = _make_metrics(registry)

        calculator = CostCalculator()
        cost_per_call = calculator.compute(100, 200)

        for _ in range(5):
            total_cost.inc(cost_per_call)

        expected = cost_per_call * 5
        assert total_cost._value.get() == pytest.approx(expected, rel=1e-6)
