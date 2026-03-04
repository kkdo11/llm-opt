"""QueueMetricsCollector 단위 테스트.

테스트 범위:
- enter() / exit() 호출 시 큐 깊이 증감
- exit() 음수 보호 (0 이하로 내려가지 않음)
- _record_sample() 이동평균 계산 정확성
- get_snapshot() 반환값 형식
- start() / stop() 태스크 생명주기
"""

import asyncio

import pytest

from src.metrics.queue_metrics import QueueMetricsCollector


@pytest.fixture
def collector() -> QueueMetricsCollector:
    """독립적인 QueueMetricsCollector 인스턴스를 제공한다."""
    return QueueMetricsCollector()


# ── 기본 enter/exit 동작 ──────────────────────────────────────────────────────

def test_enter_increases_depth(collector: QueueMetricsCollector) -> None:
    """enter() 호출 시 큐 깊이가 1 증가한다."""
    assert collector._current_depth == 0
    collector.enter()
    assert collector._current_depth == 1


def test_exit_decreases_depth(collector: QueueMetricsCollector) -> None:
    """exit() 호출 시 큐 깊이가 1 감소한다."""
    collector.enter()
    collector.enter()
    assert collector._current_depth == 2
    collector.exit()
    assert collector._current_depth == 1


def test_exit_does_not_go_negative(collector: QueueMetricsCollector) -> None:
    """exit()를 enter() 없이 호출해도 음수가 되지 않는다."""
    assert collector._current_depth == 0
    collector.exit()  # 잘못된 순서로 호출
    assert collector._current_depth == 0


def test_multiple_enter_exit(collector: QueueMetricsCollector) -> None:
    """여러 enter/exit 조합이 올바르게 동작한다."""
    for _ in range(5):
        collector.enter()
    assert collector._current_depth == 5

    for _ in range(3):
        collector.exit()
    assert collector._current_depth == 2

    for _ in range(2):
        collector.exit()
    assert collector._current_depth == 0


# ── 이동평균 계산 ─────────────────────────────────────────────────────────────

def test_record_sample_initial_empty(collector: QueueMetricsCollector) -> None:
    """샘플 수집 전에는 avg가 0이다."""
    snapshot = collector.get_snapshot()
    assert snapshot["avg_1m"] == 0.0
    assert snapshot["avg_5m"] == 0.0


def test_record_sample_single(collector: QueueMetricsCollector) -> None:
    """단일 샘플의 이동평균은 현재 값과 같다."""
    collector.enter()
    collector.enter()
    collector._record_sample()

    snapshot = collector.get_snapshot()
    assert snapshot["avg_1m"] == 2.0
    assert snapshot["avg_5m"] == 2.0


def test_record_sample_moving_average(collector: QueueMetricsCollector) -> None:
    """이동평균은 최근 샘플의 평균값이다."""
    # 샘플 3개: 10, 20, 30 → 평균 20
    collector._current_depth = 10
    collector._record_sample()

    collector._current_depth = 20
    collector._record_sample()

    collector._current_depth = 30
    collector._record_sample()

    snapshot = collector.get_snapshot()
    assert snapshot["avg_1m"] == pytest.approx(20.0, abs=0.01)
    assert snapshot["avg_5m"] == pytest.approx(20.0, abs=0.01)


def test_record_sample_window_1m_limit(collector: QueueMetricsCollector) -> None:
    """1분 윈도우(12개 샘플)를 초과하면 오래된 샘플이 제거된다."""
    # 12개 초과 샘플 추가: 0으로 12개, 100으로 1개
    for _ in range(12):
        collector._current_depth = 0
        collector._record_sample()

    collector._current_depth = 100
    collector._record_sample()

    snapshot = collector.get_snapshot()
    # 1분 윈도우: [0×11 + 100] / 12 = 100/12 ≈ 8.33
    expected_avg_1m = 100 / 12
    assert snapshot["avg_1m"] == pytest.approx(expected_avg_1m, abs=0.1)
    # 5분 윈도우: [0×12 + 100] / 13 ≈ 7.69
    expected_avg_5m = 100 / 13
    assert snapshot["avg_5m"] == pytest.approx(expected_avg_5m, abs=0.1)


# ── get_snapshot 형식 ─────────────────────────────────────────────────────────

def test_get_snapshot_keys(collector: QueueMetricsCollector) -> None:
    """get_snapshot()이 필수 키를 모두 포함한다."""
    snapshot = collector.get_snapshot()
    assert "current_depth" in snapshot
    assert "avg_1m" in snapshot
    assert "avg_5m" in snapshot
    assert "samples_1m_count" in snapshot
    assert "samples_5m_count" in snapshot


def test_get_snapshot_current_depth(collector: QueueMetricsCollector) -> None:
    """get_snapshot()의 current_depth가 실제 큐 깊이와 일치한다."""
    collector.enter()
    collector.enter()
    collector.enter()
    snapshot = collector.get_snapshot()
    assert snapshot["current_depth"] == 3


# ── 백그라운드 태스크 생명주기 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_creates_task(collector: QueueMetricsCollector) -> None:
    """start()가 백그라운드 태스크를 생성한다."""
    assert collector._task is None
    collector.start()
    assert collector._task is not None
    assert not collector._task.done()
    collector.stop()


@pytest.mark.asyncio
async def test_stop_cancels_task(collector: QueueMetricsCollector) -> None:
    """stop()이 백그라운드 태스크를 취소한다."""
    collector.start()
    collector.stop()
    # 취소 후 task가 None이 되거나 done 상태
    assert collector._task is None or collector._task.done()


@pytest.mark.asyncio
async def test_start_idempotent(collector: QueueMetricsCollector) -> None:
    """start()를 중복 호출해도 태스크가 하나만 생성된다."""
    collector.start()
    task_first = collector._task
    collector.start()  # 두 번째 호출
    task_second = collector._task
    assert task_first is task_second  # 동일 태스크
    collector.stop()


@pytest.mark.asyncio
async def test_sample_collected_after_interval(collector: QueueMetricsCollector) -> None:
    """백그라운드 루프가 실행되면 샘플이 수집된다.

    테스트용으로 _SAMPLE_INTERVAL_SECONDS를 0.1로 줄여서 확인한다.
    """
    collector._SAMPLE_INTERVAL_SECONDS = 0.1
    collector._current_depth = 5

    collector.start()
    await asyncio.sleep(0.25)  # 2회 이상 샘플 수집 대기
    collector.stop()

    snapshot = collector.get_snapshot()
    assert snapshot["samples_1m_count"] >= 2
    assert snapshot["avg_1m"] == pytest.approx(5.0, abs=0.01)
