# LLM-OPT 테스트 개선 계획

> 마지막 업데이트: 2026-04-08
> 목적: 딥오토 지원 전 이력서 수치 강화 — main.py 통합 테스트 추가

## 지금 당장 할 것
- 없음 (전체 완료)

## 막힌 것 / 미결 사항
- 없음

---

## 성공 기준

| 지표 | 시작 | 목표 | 최종 (실측) |
|------|------|------|------------|
| 전체 커버리지 | 85% | 90%+ | **91%** ✅ |
| main.py 커버리지 | 66% | 85%+ | **85%** ✅ |
| pytest 경고 | 16개 | 0개 | **0개** ✅ |
| 테스트 수 | 151개 | 157개+ | **166개** ✅ |
| RuntimeWarning | 1개 | 0개 | **0개** ✅ |

---

## Step별 진행 현황

### Step 1: datetime.utcnow() deprecated 수정 ✅
완료 기준: pytest 경고 16개 → 3개 이하
- [x] `quota_tracker.py` 44줄, 58줄 `utcnow()` → `now(timezone.utc)` 교체

### Step 2: vector_cache store mock 버그 수정 ✅
완료 기준: RuntimeWarning 0개, 기존 3개 테스트 통과
- [x] `test_vector_cache.py` pipe_mock 구조 수정 (AsyncMock → MagicMock)

### Step 3: test_proxy.py 통합 테스트 추가 ✅
완료 기준: main.py 커버리지 85%+, 테스트 통과
- [x] test_l2_semantic_hit_returns_cached_response
- [x] test_l2_hit_validation_fails_falls_through_to_llm
- [x] test_l2_miss_stores_to_both_caches
- [x] test_quota_exceeded_returns_429
- [x] test_stream_l1_hit_returns_sse
- [x] test_long_query_skips_l2
- [x] test_cache_not_initialized_returns_503
- [x] test_l2_stream_hit_returns_sse
- [x] test_quota_warning_continues_to_llm
- [x] test_stream_llm_increments_quota_and_stores_l2
- [x] test_non_stream_llm_increments_quota
- [x] test_health_with_llm_backend
- [x] test_detect_lang_returns_empty_on_error
- [x] test_get_embedding_raises_when_model_none
- [x] test_get_embedding_returns_vector_when_model_set

### Step 4: 커버리지 재측정 및 확인 ✅
완료 기준: 전체 90%+, main.py 85%+, 경고 0개
- [x] 전체 91%, main.py 85%, 경고 0개, 테스트 166개 확인

---

## 완료된 결정사항
- 실 Redis / 실 Ollama 연동 테스트는 이번 범위 제외 (외부 의존성)
- k6 부하 테스트는 이번 범위 제외
- 새 기능(Celery, 모델 라우팅) 관련 테스트는 이번 범위 제외
