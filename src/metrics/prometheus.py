"""Prometheus 메트릭 정의.

메트릭 목록:
- api_calls_total: LLM 실제 호출 횟수 (캐시 미스 시 증가)
- cache_hits_total: 캐시 히트 횟수 (tier 라벨: l1_hash)
- latency_seconds: 요청 전체 응답 시간 히스토그램
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
