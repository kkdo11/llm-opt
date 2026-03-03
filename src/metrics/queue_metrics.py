"""LLM 요청 큐 메트릭 — 이동평균 기반 HPA 스케일링 지표 제공.

설계 목적:
  HPA가 "현재 순간 요청 수"가 아닌 "최근 N분간 평균 요청 수"를 기준으로
  스케일링하도록 이동평균(moving average)을 Prometheus Gauge로 노출한다.

  단순 동시접속 수가 아니라 LLM 백엔드 호출 큐(캐시 미스 후 대기 중인 요청)를
  측정한다. 캐시 히트 요청은 큐에 진입하지 않으므로 스케일링 불필요.

메트릭:
  llm_queue_depth           : 현재 처리 중인 LLM 요청 수 (Gauge)
  llm_queue_depth_avg_1m    : 1분 이동평균 (Gauge) — HPA Scale Up 기준
  llm_queue_depth_avg_5m    : 5분 이동평균 (Gauge) — HPA Scale Down 기준

스케일링 정책 (hpa.yaml에서 참조):
  Scale Up  : 1분평균 > 20 → 30초 이내 포드 추가
  Scale Down: 5분평균 < 5  → 5분 안정화 후 포드 제거
"""

import asyncio
import time
from collections import deque

from prometheus_client import Gauge

# ── Prometheus Gauge 정의 ───────────────────────────────────────────────────

queue_depth = Gauge(
    "llm_queue_depth",
    "현재 처리 중인 LLM 백엔드 요청 수 (캐시 미스 → LLM 대기/실행 중)",
)

queue_depth_avg_1m = Gauge(
    "llm_queue_depth_avg_1m",
    "LLM 요청 큐 깊이 1분 이동평균 — HPA Scale Up 기준 메트릭",
)

queue_depth_avg_5m = Gauge(
    "llm_queue_depth_avg_5m",
    "LLM 요청 큐 깊이 5분 이동평균 — HPA Scale Down 기준 메트릭",
)


class QueueMetricsCollector:
    """LLM 요청 큐 깊이를 추적하고 이동평균을 계산한다.

    사용 예시::

        collector = QueueMetricsCollector()

        # LLM 호출 직전
        collector.enter()
        try:
            result = await llm_backend.chat(...)
        finally:
            # LLM 호출 완료 (성공/실패 무관)
            collector.exit()

    스냅샷 수집 주기는 _SAMPLE_INTERVAL_SECONDS 마다 백그라운드 태스크가 실행.
    이동평균 윈도우:
      - 1분: 최근 60초 / 5초 간격 = 12개 샘플
      - 5분: 최근 300초 / 5초 간격 = 60개 샘플
    """

    _SAMPLE_INTERVAL_SECONDS: float = 5.0
    _WINDOW_1M_SAMPLES: int = 12   # 60s / 5s
    _WINDOW_5M_SAMPLES: int = 60   # 300s / 5s

    def __init__(self) -> None:
        self._current_depth: int = 0
        self._samples_1m: deque[int] = deque(maxlen=self._WINDOW_1M_SAMPLES)
        self._samples_5m: deque[int] = deque(maxlen=self._WINDOW_5M_SAMPLES)
        self._task: asyncio.Task | None = None

    # ── 큐 진입/종료 ─────────────────────────────────────────────────────────

    def enter(self) -> None:
        """LLM 요청이 큐에 진입할 때 호출 (캐시 미스 확정 직후)."""
        self._current_depth += 1
        queue_depth.set(self._current_depth)

    def exit(self) -> None:
        """LLM 요청이 큐에서 나올 때 호출 (LLM 응답 반환 또는 에러 발생 후)."""
        self._current_depth = max(0, self._current_depth - 1)
        queue_depth.set(self._current_depth)

    # ── 이동평균 갱신 ─────────────────────────────────────────────────────────

    def _record_sample(self) -> None:
        """현재 큐 깊이를 샘플로 기록하고 이동평균을 갱신한다."""
        depth = self._current_depth
        self._samples_1m.append(depth)
        self._samples_5m.append(depth)

        avg_1m = sum(self._samples_1m) / len(self._samples_1m)
        avg_5m = sum(self._samples_5m) / len(self._samples_5m)

        queue_depth_avg_1m.set(avg_1m)
        queue_depth_avg_5m.set(avg_5m)

    # ── 백그라운드 수집 태스크 ────────────────────────────────────────────────

    async def _collect_loop(self) -> None:
        """5초마다 샘플을 수집하는 백그라운드 루프."""
        while True:
            await asyncio.sleep(self._SAMPLE_INTERVAL_SECONDS)
            self._record_sample()

    def start(self) -> None:
        """백그라운드 수집 태스크를 시작한다.

        FastAPI lifespan의 yield 이전에 호출해야 한다.
        """
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._collect_loop())

    def stop(self) -> None:
        """백그라운드 수집 태스크를 중단한다.

        FastAPI lifespan의 yield 이후에 호출해야 한다.
        """
        if self._task is not None and not self._task.done():
            self._task.cancel()
            self._task = None

    # ── 현재 상태 조회 ────────────────────────────────────────────────────────

    def get_snapshot(self) -> dict:
        """현재 메트릭 스냅샷을 반환한다 (디버깅 및 /health 엔드포인트용)."""
        avg_1m = (
            sum(self._samples_1m) / len(self._samples_1m)
            if self._samples_1m else 0.0
        )
        avg_5m = (
            sum(self._samples_5m) / len(self._samples_5m)
            if self._samples_5m else 0.0
        )
        return {
            "current_depth": self._current_depth,
            "avg_1m": round(avg_1m, 2),
            "avg_5m": round(avg_5m, 2),
            "samples_1m_count": len(self._samples_1m),
            "samples_5m_count": len(self._samples_5m),
        }


# 싱글톤 인스턴스 (main.py에서 import하여 사용)
queue_metrics = QueueMetricsCollector()
