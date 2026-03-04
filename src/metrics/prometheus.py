"""Prometheus 메트릭 정의.

메트릭 목록:
- api_calls_total:        LLM 실제 호출 횟수 (캐시 미스 시 증가)
- cache_hits_total:       캐시 히트 횟수 (tier 라벨: l1_hash / l2_semantic)
- latency_seconds:        요청 전체 응답 시간 히스토그램
- total_cost_usd:         LLM 실제 호출로 발생한 누적 비용 (USD) — Phase 5
- cost_saved_usd:         캐시 히트로 절감된 누적 비용 (USD, tier 라벨) — Phase 5
- tokens_total:           누적 토큰 사용량 (type 라벨: input | output) — Phase 5
- llm_queue_depth:        현재 LLM 큐 깊이 (→ queue_metrics.py에서 정의)
- llm_queue_depth_avg_1m: 1분 이동평균 (HPA Scale Up 기준)
- llm_queue_depth_avg_5m: 5분 이동평균 (HPA Scale Down 기준)

큐 관련 Gauge는 queue_metrics.py에서 정의되며,
이 파일은 api_calls_total / cache_hits_total / latency_seconds 및
Phase 5 비용/토큰 메트릭을 정의한다.
"""

from prometheus_client import Counter, Histogram

# LLM 실제 호출 횟수 (캐시 미스 → Ollama/mock 호출)
api_calls_total = Counter(
    "llm_api_calls_total",
    "LLM 백엔드 실제 호출 횟수",
)

# 캐시 히트 횟수 (tier: l1_hash / l2_semantic - Phase 2에서 추가)
cache_hits_total = Counter(
    "llm_cache_hits_total",
    "캐시 히트 횟수",
    labelnames=["tier"],
)

# 응답 시간 히스토그램 (cache_status: hit / miss)
latency_seconds = Histogram(
    "llm_request_latency_seconds",
    "요청 전체 처리 시간(초)",
    labelnames=["cache_status"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# Phase 5: 실제 LLM 호출 비용 누적 Counter
total_cost_usd = Counter(
    "llm_total_cost_usd_total",
    "LLM 실제 호출로 발생한 누적 비용 (USD)",
)

# Phase 5: 캐시 히트로 절감된 비용 누적 (tier별)
cost_saved_usd = Counter(
    "llm_cost_saved_usd_total",
    "캐시 히트로 절감된 누적 비용 (USD)",
    labelnames=["tier"],  # l1_hash | l2_semantic
)

# Phase 5: 누적 토큰 사용량 (type별)
tokens_total = Counter(
    "llm_tokens_total",
    "누적 토큰 사용량",
    labelnames=["type"],  # input | output
)
