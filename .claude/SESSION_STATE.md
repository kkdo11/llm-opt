# 세션 상태 — 재시작 후 즉시 읽기

> 이 파일은 Claude Code 세션 재시작 후 컨텍스트를 빠르게 복구하기 위한 문서입니다.
> 마지막 업데이트: 2026-02-22

---

## 현재 위치

**브랜치**: `phase-2/semantic-cache`
**다음 작업**: Phase 4 시작

---

## 완료된 Phase 요약

### Phase 1 ✅ (커밋 완료)
- Redis Hash Cache (L1) 구현
- 실측: 6,065ms → 0.3ms, Hit 50%, API 호출 50% 감소

### Phase 2 ✅ (커밋 완료)
- Semantic Cache (L2): HNSW + SentenceTransformer
- 모델: `paraphrase-multilingual-MiniLM-L12-v2` (384차원)
- Validation Layer: 언어 / 숫자 키워드 / 기술 키워드 3단계
- 실측: Hit Ratio 66.7%, FP 0%, threshold=0.75

### Phase 3 ✅ (구현 완료, **커밋 미완료**)
- 토큰 예측: Rule-based (SHORT 150 / MEDIUM 400 / LONG 800 / VERY_LONG 1200)
- 비용 계산: input $0.0005/1K, output $0.0015/1K (환경변수 오버라이드)
- 할당량: `llm:quota:{user_id}:{YYYYMM}`, EXPIREAT 월말, 80%=WARNING, 100%=HTTP429
- SSE 스트리밍: 예측 상한 × 1.5 초과 시 [TRUNCATED]
- 테스트: 107개 통과 (test_token_predictor 20개 + test_quota_tracker 14개 신규)

---

## 커밋되지 않은 변경사항 (Phase 3)

```bash
# 신규 파일
src/proxy/cost/__init__.py
src/proxy/cost/token_predictor.py
src/proxy/cost/cost_calculator.py
src/proxy/rate_limit/__init__.py
src/proxy/rate_limit/quota_tracker.py
tests/unit/test_token_predictor.py
tests/unit/test_quota_tracker.py
docs/GROWTH.md
docs/TECH_KNOWLEDGE.md
docs/blog/phase1-redis-cache-20000x.md
docs/blog/phase2-embedding-model-selection.md
docs/blog/phase2-semantic-cache-langdetect.md

# 수정된 파일
src/proxy/main.py          # QuotaTracker, streaming, quota check, response_model=None
src/proxy/models.py        # user_id, tokens_used, cost_usd 필드 추가
tests/unit/test_proxy.py   # mock_cache fixture에 _quota_tracker=None 격리
docs/PHASE_TRACKER.md      # Phase 3 완료 기록
```

커밋 전 확인: `git status --short` → `pytest tests/ -q`

---

## Phase 4 시작 가이드

### 목표
- Custom Metrics Exporter (Prometheus Gauge)
- HPA: 1분 이동평균 > 20 → Scale Up, 5분 이동평균 < 5 → Scale Down
- k6 부하 테스트: 정상 / 증가 / 피크 / Spike / Soak 시나리오
- 성공 기준: 1,000 동시접속 에러율 < 1%, HPA 응답 < 60초, P95 < 2초

### 구현 예정 파일
```
src/metrics/exporter.py        # Prometheus Gauge, 이동평균 계산
src/scaling/hpa_config.py      # 비대칭 스케일링 정책
k8s/deployment.yaml
k8s/hpa.yaml
k8s/configmap.yaml
tests/load/scenario_basic.js   # k6 스크립트
```

### Phase 4 브랜치 전략
```bash
git checkout -b phase-4/k8s-hpa
```

---

## 핵심 설계 결정 (참조용)

| 항목 | 결정 | 이유 |
|------|------|------|
| 임베딩 모델 | paraphrase-multilingual-MiniLM-L12-v2 | 384차원 유지, 한국어 paraphrase 0.87~0.95 |
| KNN k | k=3 | k=1은 구조 유사 원본 잘못 매칭 |
| threshold | 0.75 | 0.80 이상은 hit 0%, 0.75에서 FP 실질 0% |
| 토큰 추정 | UTF-8 바이트 / 4 | Qwen tokenizer ≠ tiktoken, ±30% 오차 허용 |
| 할당량 만료 | EXPIREAT 월말 | 매달 자동 초기화, cron 불필요 |
| response_model | None | ChatResponse \| StreamingResponse Union 반환 |

---

## 주요 환경변수

```bash
LLM_MODE=mock              # mock | ollama
REDIS_URL=redis://localhost:6379
SEMANTIC_CACHE_ENABLED=true
EMBEDDING_MODEL=paraphrase-multilingual-MiniLM-L12-v2
SIMILARITY_THRESHOLD=0.75
USER_QUOTA_TOKENS=100000
COST_PER_INPUT_1K=0.0005
COST_PER_OUTPUT_1K=0.0015
```

---

## 테스트 실행

```bash
# 전체 (107개, ~5초)
pytest tests/ -q

# Phase 3 신규만
pytest tests/unit/test_token_predictor.py tests/unit/test_quota_tracker.py -v

# 커버리지
pytest tests/unit/ -v --cov=src --cov-report=term-missing
```

---

## 블로그 현황

| 파일 | 상태 |
|------|------|
| docs/blog/phase1-redis-cache-20000x.md | ✅ 작성 완료 (tistory 업로드 가능) |
| docs/blog/phase2-embedding-model-selection.md | ✅ 작성 완료 |
| docs/blog/phase2-semantic-cache-langdetect.md | ✅ 작성 완료 |
| Phase 3 블로그 | ⬜ 미작성 (Phase 3 완료 후 작성 예정) |

---

## 트러블슈팅 빠른 참조

| 문제 | 해결 |
|------|------|
| FastAPI `Union` 반환 오류 | `@app.post(..., response_model=None)` |
| pytest event loop mismatch | `proxy_main._quota_tracker = None` in fixture |
| langdetect 에스토니아어 오인식 | `normalize_query()` 후 detect |
| `\b` 한국어 미매칭 | `re.compile(..., re.ASCII)` |
| KNN 잘못된 원본 매칭 | k=3, Validation Layer 통과 첫 번째 선택 |
