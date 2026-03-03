# 세션 상태 — 재시작 후 즉시 읽기

> 이 파일은 Claude Code 세션 재시작 후 컨텍스트를 빠르게 복구하기 위한 문서입니다.
> 마지막 업데이트: 2026-03-03

---

## 현재 위치

**브랜치**: `phase-4/k8s`
**현재 상태**: Phase 4 구현 완료 (K8s manifests + HPA + k6 + Custom Metrics). **다음 작업: Phase 5 (Grafana 대시보드)**

---

## 완료된 작업 전체 요약

### Phase 1 ✅ (커밋 완료, 푸시 완료)
- Redis Hash Cache (L1) 구현
- 실측: 6,065ms → 0.3ms, Hit 50%, API 호출 50% 감소

### Phase 2 ✅ (커밋 완료, 푸시 완료)
- Semantic Cache (L2): HNSW + SentenceTransformer
- 모델: `paraphrase-multilingual-MiniLM-L12-v2` (384차원)
- Validation Layer: 언어 / 숫자 키워드 / 기술 키워드 3단계
- 실측: Hit Ratio 66.7%, FP 0%, threshold=0.75

### Phase 3 ✅ (커밋 완료, 푸시 완료)
- 토큰 예측: Rule-based (SHORT 150 / MEDIUM 400 / LONG 800 / VERY_LONG 1200)
- 비용 계산: input $0.0005/1K, output $0.0015/1K (환경변수 오버라이드 가능)
- 할당량: `llm:quota:{user_id}:{YYYYMM}`, EXPIREAT 월말, 80%=WARNING, 100%=HTTP 429
- SSE 스트리밍: 예측 상한 × 1.5 초과 시 [TRUNCATED]
- 테스트: 107개 통과

### Phase A ✅ (커밋 완료)
**A-1: 멀티 백엔드 추상화**
- `src/backends/base.py`: LLMBackend ABC + LLMResponse dataclass
- `src/backends/ollama_backend.py`: Ollama OpenAI 호환 API (기존 로직 이관)
- `src/backends/openai_backend.py`: OpenAI API 백엔드 (선택적)
- `main.py`: `_process_chat()` 추출, `LLM_BACKEND` 환경변수로 DI

**A-2: Ollama 호환 엔드포인트**
- `src/proxy/routes/ollama_compat.py`: `/api/chat`, `/api/generate`
- SSE → NDJSON 변환 (`_sse_to_ndjson`)
- 테스트: 128개 전체 통과 (기존 107 + 신규 21)

**A-3: 실 MindGraph 연결 검증**
- `application.properties`: chat-model base-url → `http://localhost:8000`
- `LangChainConfig.java`: embedding base-url 분리 (`embeddingBaseUrl` 별도 필드)
  - 버그: chat/embedding 모두 `langchain4j.ollama.chat-model.base-url` 사용
  - 수정: `@Value("${langchain4j.ollama.embedding-model.base-url}")` 주입
- 실측: L1 캐시 히트 0.43ms (vs LLM 5,510ms), Redis에 `llm:cache:*` 5개 저장

### 문서화 ✅
- `docs/PHASE_TRACKER.md`, `docs/TECH_KNOWLEDGE.md`, `docs/GROWTH.md`
- `docs/blog/` — 블로그 포스트 6편 (Phase 1~2)
- `docs/portfolio.md` (미커밋)
- `../RESUME_PROJECTS.md`

---

### Phase B ✅ (mindgraph-ai 측 작업 — git 미설정 프로젝트)
- `neo4j/entity/KnowledgeNode.java` 신규, `Person.java` 삭제
- `GraphService.java`: `syncToNeo4j()` — Neo4jClient MERGE
- `MindGraphService.java`: `searchNeo4jTwoHop()` — 2-hop Cypher
- 실측: PostgreSQL+Neo4j 동기화 ✅, 2-hop RAG 확장 ✅, 50개 테스트 통과 ✅

### Phase 4 ✅ 구현 완료 (브랜치: phase-4/k8s)
- `src/metrics/queue_metrics.py`: QueueMetricsCollector (enter/exit, 이동평균, Prometheus Gauge)
- `src/metrics/prometheus.py`: 주석 업데이트
- `src/proxy/main.py`: queue_metrics 통합 (lifespan start/stop, LLM 호출 enter/exit, /health 스냅샷)
- `k8s/configmap.yaml`, `deployment.yaml`, `service.yaml`, `hpa.yaml`, `prometheus-adapter-config.yaml`
- `tests/load/`: common.js, scenario_baseline.js, scenario_ramp.js, scenario_spike.js, scenario_soak.js
- `tests/unit/test_queue_metrics.py`: 14개 통과, 전체 142개 통과
- 실 K8s 배포 및 부하 테스트 측정은 미완 (설계 및 코드 완료)

## 커밋 필요한 파일 (llm-opt)

```bash
cd /home/kdw03/projects/llm-opt
git status --short
# 미커밋:
#  M .claude/SESSION_STATE.md
#  M docs/PHASE_TRACKER.md
#  M src/proxy/main.py
# ?? docs/INTERVIEW_PREP.md
# ?? docs/blog/phase2-fastapi-testclient-lifespan.md
# ?? docs/blog/phase2-redis-hnsw-knn-matching.md
# ?? docs/blog/phase2-regex-unicode-boundary.md
# ?? docs/portfolio.md
# ?? src/backends/
# ?? src/proxy/routes/
# ?? tests/unit/test_backends.py
# ?? tests/unit/test_ollama_compat.py

# Phase A 전체 커밋
git add src/backends/ src/proxy/routes/ src/proxy/main.py \
        tests/unit/test_backends.py tests/unit/test_ollama_compat.py \
        docs/PHASE_TRACKER.md docs/INTERVIEW_PREP.md docs/portfolio.md \
        docs/blog/phase2-fastapi-testclient-lifespan.md \
        docs/blog/phase2-redis-hnsw-knn-matching.md \
        docs/blog/phase2-regex-unicode-boundary.md \
        .claude/SESSION_STATE.md
git commit -m "feat: Phase A — 멀티 백엔드 추상화 + Ollama 호환 엔드포인트 + MindGraph 연결 완료"
```

---

## 다음 작업: Phase 5 — Grafana 대시보드 + 실시간 비용 시각화

```
- Prometheus 메트릭 정의 보강 (비용 Counter, 토큰 Counter)
- Grafana 대시보드 JSON (5개 패널)
  - 패널 1: 실시간 비용 누적 (actual vs saved)
  - 패널 2: Cache Hit Ratio (L1/L2 tier별)
  - 패널 3: 비용 비교 (캐시 유/무)
  - 패널 4: 레이턴시 분포 (P50/P95/P99)
  - 패널 5: Pod Autoscaling (HPA replicas + queue depth)
- 브랜치: phase-5/grafana
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
| L2 백필 → L1 | L2 히트 시 L1에도 저장 | 동일 표현 재발 시 0.3ms로 처리 |
| Embedding 직접 호출 | 프록시 우회 | 입력이 매번 다른 원문; 캐싱 이점 없음 |

---

## 주요 환경변수

```bash
LLM_MODE=ollama                  # mock | ollama
LLM_BACKEND=ollama               # ollama | openai
OLLAMA_BASE_URL=http://localhost:11434/v1
REDIS_URL=redis://localhost:6379
SEMANTIC_CACHE_ENABLED=true
EMBEDDING_MODEL=paraphrase-multilingual-MiniLM-L12-v2
SEMANTIC_THRESHOLD=0.75
USER_QUOTA_TOKENS=100000
COST_PER_INPUT_1K=0.0005
COST_PER_OUTPUT_1K=0.0015
```

---

## 테스트 실행

```bash
cd /home/kdw03/projects/llm-opt
pytest tests/ -q          # 전체 128개, ~5초
pytest tests/unit/ -v --cov=src --cov-report=term-missing
```

---

## 트러블슈팅 빠른 참조

| 문제 | 해결 |
|------|------|
| FastAPI `Union` 반환 오류 | `@app.post(..., response_model=None)` |
| pytest event loop mismatch | `proxy_main._quota_tracker = None` in fixture |
| langdetect 에스토니아어 오인식 | `normalize_query()` 후 detect |
| `\b` 한국어 미매칭 | `re.compile(..., re.ASCII)` |
| KNN 잘못된 원본 매칭 | k=3, Validation Layer 통과 첫 번째 선택 |
| TestClient lifespan mock 덮어씀 | `AsyncClient + ASGITransport` (비스트리밍) |
| MindGraph embedding 404 | `LangChainConfig.java` embeddingBaseUrl 분리 |

---

## 블로그 현황

| 파일 | 상태 |
|------|------|
| docs/blog/phase1-redis-cache-20000x.md | ✅ 작성 완료 |
| docs/blog/phase2-embedding-model-selection.md | ✅ 작성 완료 |
| docs/blog/phase2-semantic-cache-langdetect.md | ✅ 작성 완료 |
| docs/blog/phase2-regex-unicode-boundary.md | ✅ 작성 완료 |
| docs/blog/phase2-fastapi-testclient-lifespan.md | ✅ 작성 완료 |
| docs/blog/phase2-redis-hnsw-knn-matching.md | ✅ 작성 완료 |
| Phase 3 블로그 | ⬜ 미작성 |
| Phase A 블로그 | ⬜ 미작성 |

---

## 관련 프로젝트

**MindGraph-AI** (`/home/kdw03/projects/mindgraph-ai/`)
- 이 프로젝트의 소비자 — Chat 트래픽을 LLM-OPT 프록시로 연결 **완료**
- 수정 파일: `LangChainConfig.java` (embeddingBaseUrl 분리), `application.properties` (base-url 변경)
- 세션 상태: `mindgraph-ai/.claude/SESSION_STATE.md` 참조
- 통합 이력서: `../RESUME_PROJECTS.md`
