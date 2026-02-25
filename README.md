# LLM-OPT

> **Inference Traffic Optimization Platform**
> Semantic Caching + 트래픽 제어를 통한 LLM 운영 비용 최적화 프록시

[![Python](https://img.shields.io/badge/Python-3.11+-blue)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)](https://fastapi.tiangolo.com)
[![Redis Stack](https://img.shields.io/badge/Redis_Stack-7.4-red)](https://redis.io/docs/stack/)
[![Tests](https://img.shields.io/badge/Tests-107_passed-brightgreen)](#테스트)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

---

## 왜 만들었나

[MindGraph-AI](../mindgraph-ai) 프로젝트에서 **Qwen 2.5 14B**(RTX 4080 Super, 16GB VRAM)를 운영하다가 발견한 두 가지 비효율:

1. **완전히 동일한 질문**이 반복되는데 매번 6초짜리 LLM 추론 발생
2. **"파이썬 버블소트 코드 짜줘"** 같이 표현만 다른 질문도 캐시 미스로 처리

이를 해결하기 위해 LLM 앞단에 **Semantic Caching 프록시**를 직접 설계·구현했습니다.

---

## 실측 성능

> 모든 수치는 Qwen 2.5 14B (Ollama, RTX 4080 Super) 환경에서 직접 측정한 값입니다.

| 경로 | 응답 시간 | LLM 호출 여부 |
|------|---------|-------------|
| L1 Hash Cache Hit | **0.3ms** | ❌ (20,217배 빠름) |
| L2 Semantic Cache Hit | **25.5ms** | ❌ (238배 빠름) |
| LLM 직접 호출 (Miss) | 6,065ms | ✅ |

| 지표 | Phase 1 | Phase 2 |
|------|---------|---------|
| Cache Hit Ratio | 50.0% | **66.7%** |
| False Positive Rate | — | **0%** |
| 월간 비용 절감 (예상) | — | **56.8%** ($682/월, 일 10,000건 기준) |

---

## 아키텍처

```
[MindGraph-AI / 클라이언트]
        │
        │ POST /v1/chat/completions
        ▼
┌─────────────────────────────────────┐
│           FastAPI Proxy             │
│                                     │
│  ① L1 Hash Cache                   │
│     MD5(messages) → Redis String   │
│     Hit: 0.3ms ──────────────────► │ 응답 반환
│                                     │
│  ② L2 Semantic Cache               │
│     normalize → embed → HNSW KNN  │
│     Validation (언어/숫자/기술)     │
│     Hit: 25.5ms ─────────────────► │ 응답 반환 + L1 백필
│                                     │
│  ③ Quota 확인 (월간 토큰 한도)     │
│     EXCEEDED → HTTP 429            │
│                                     │
│  ④ 토큰 예측 + LLM 호출            │
│     Rule-based 유형 분류           │
│     streaming 모니터링 (150% 차단) │
│                                     │
│  ⑤ 캐시 저장 + 메트릭              │
│     L1 + L2 저장 / Prometheus      │
└──────────────────┬──────────────────┘
                   │
                   ▼
          [Ollama :11434]
          Qwen 2.5 14B
```

### Validation Layer (False Positive 방지)

유사도 임계값(0.75)만으로는 부족한 케이스를 3단계로 차단합니다:

```
1단계: 언어 일치   "Python 정렬" vs "Java 정렬" → lang 불일치 → Miss ✅
2단계: 숫자/버전   "2024년 GDP" vs "2023년 GDP" → 숫자 불일치 → Miss ✅
3단계: 기술 키워드  "TCP/UDP 차이" vs "HTTP/HTTPS 차이" → 프로토콜 불일치 → Miss ✅
```

---

## 빠른 시작

### 요구사항

- Python 3.11+
- Docker & Docker Compose
- (선택) Ollama — 없으면 `LLM_MODE=mock`으로 테스트 가능

### 설치 및 실행

```bash
# 1. 저장소 클론
git clone https://github.com/kkdo11/llm-opt.git
cd llm-opt

# 2. 의존성 설치
pip install -r requirements.txt

# 3. Redis Stack 기동
docker compose up redis -d

# 4. 프록시 서버 실행 (mock 모드 — Ollama 없이 테스트)
LLM_MODE=mock uvicorn src.proxy.main:app --reload --port 8000

# 5. 헬스체크
curl http://localhost:8000/health
```

### 실제 LLM 연결 (Ollama)

```bash
# Ollama 설치 후 모델 다운로드
ollama pull qwen2.5:14b

# 환경변수 설정 후 실행
LLM_MODE=ollama \
OLLAMA_BASE_URL=http://localhost:11434/v1 \
uvicorn src.proxy.main:app --reload --port 8000
```

### API 사용 예시

```bash
# 첫 번째 요청 (Miss → LLM 호출)
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen2.5:14b",
    "messages": [{"role": "user", "content": "파이썬으로 버블 정렬 구현해줘"}],
    "user_id": "user1"
  }'
# 응답: {"cached": false, "tier": null, "latency_ms": 6065, ...}

# 두 번째 요청 — 표현만 다른 유사 질문 (L2 Hit)
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen2.5:14b",
    "messages": [{"role": "user", "content": "파이썬 버블소트 코드 짜줘"}],
    "user_id": "user1"
  }'
# 응답: {"cached": true, "tier": "l2_semantic", "latency_ms": 25.5, ...}
```

---

## 프로젝트 구조

```
llm-opt/
├── src/
│   ├── proxy/
│   │   ├── main.py                  # FastAPI 앱 + 캐시 파이프라인 (502줄)
│   │   ├── models.py                # ChatRequest / ChatResponse
│   │   ├── cache/
│   │   │   ├── redis_cache.py       # L1 Hash Cache (MD5 + Redis String)
│   │   │   ├── vector_cache.py      # L2 Semantic Cache (HNSW, 384차원)
│   │   │   └── normalizer.py        # 영어↔한글 기술 용어 정규화
│   │   ├── validation/
│   │   │   └── validator.py         # 3단계 FP 방지 (234줄)
│   │   ├── cost/
│   │   │   ├── token_predictor.py   # Rule-based 출력 토큰 예측
│   │   │   └── cost_calculator.py   # 입출력 토큰 → USD
│   │   └── rate_limit/
│   │       └── quota_tracker.py     # 월간 할당량 (Redis EXPIREAT)
│   └── metrics/
│       └── prometheus.py            # 메트릭 정의 (3종)
├── tests/unit/                      # 107개 단위 테스트
│   ├── test_cache.py
│   ├── test_vector_cache.py
│   ├── test_validator.py
│   ├── test_normalizer.py
│   ├── test_token_predictor.py
│   ├── test_quota_tracker.py
│   └── test_proxy.py
├── docs/
│   ├── PROJECT_CONTEXT.md           # 아키텍처 및 기술 결정 기록
│   └── PHASE_TRACKER.md             # Phase별 실측치 + 트러블슈팅
├── scripts/                         # 벤치마크 및 실험 스크립트
├── monitoring/
│   └── prometheus.yml
├── docker-compose.yml               # Redis Stack + Prometheus
└── .claude/                         # Claude Code 에이전트/스킬
```

---

## 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `LLM_MODE` | `mock` | `mock` \| `ollama` |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama 엔드포인트 |
| `REDIS_URL` | `redis://localhost:6379` | Redis 연결 URL |
| `SEMANTIC_CACHE_ENABLED` | `true` | L2 Semantic Cache 활성화 |
| `EMBEDDING_MODEL` | `paraphrase-multilingual-MiniLM-L12-v2` | 임베딩 모델 |
| `SEMANTIC_THRESHOLD` | `0.75` | L2 유사도 임계값 (실측 최적값) |
| `USER_QUOTA_TOKENS` | `100000` | 월간 토큰 할당량 |
| `COST_PER_INPUT_1K` | `0.0005` | 입력 토큰 단가 (USD/1K) |
| `COST_PER_OUTPUT_1K` | `0.0015` | 출력 토큰 단가 (USD/1K) |

---

## 테스트

```bash
# 전체 실행 (107개, ~5초)
pytest tests/ -v

# 커버리지 포함
pytest tests/unit/ -v --cov=src

# 특정 모듈만
pytest tests/unit/test_validator.py -v
```

외부 의존성(Redis, LLM)은 전부 `AsyncMock`으로 격리되어 **인프라 없이** 실행됩니다.

---

## 모니터링

```bash
# Prometheus + Redis 한 번에 기동
docker compose up -d

# 메트릭 확인
curl http://localhost:8000/metrics

# Prometheus UI
open http://localhost:9090
```

**수집 메트릭:**

| 메트릭 | 타입 | 설명 |
|--------|------|------|
| `llm_api_calls_total` | Counter | LLM 실제 호출 횟수 (캐시 미스) |
| `llm_cache_hits_total{tier}` | Counter | 캐시 히트 횟수 (l1_hash / l2_semantic) |
| `llm_request_latency_seconds{cache_status}` | Histogram | 요청 처리 시간 |

---

## 개발 현황

| Phase | 내용 | 상태 | 핵심 실측치 |
|-------|------|------|------------|
| Phase 1 | FastAPI Proxy + Redis Hash Cache | ✅ 완료 | Hit 50%, 0.3ms, 20,217배 |
| Phase 2 | Semantic Cache + Validation Layer | ✅ 완료 | Hit 66.7%, FP 0% |
| Phase 3 | 토큰 예측 + 비용 보호 + 할당량 | ✅ 구현완료 | 실 Ollama 측정 예정 |
| Phase A | MindGraph 연결 (Ollama 호환 엔드포인트) | 🔄 진행 예정 | — |
| Phase 4 | Kubernetes + Custom HPA + k6 | ⬜ | — |
| Phase 5 | Grafana 대시보드 + 실시간 비용 | ⬜ | — |

---

## 주요 기술 결정

| 결정 | 대안 | 근거 |
|------|------|------|
| Redis Stack (L1 + L2 통합) | Pinecone 등 전용 Vector DB | 운영 복잡도 제거, 자체 호스팅 $0 |
| paraphrase-multilingual-MiniLM-L12-v2 | all-MiniLM-L6-v2 | 한국어 paraphrase 유사도 0.32 → 0.94 |
| KNN k=3 후 Validation | k=1 단일 매칭 | 구조 유사 원본 잘못 매칭 방지 |
| Rule-based 토큰 예측 | ML 모델 | 150% 차단이 목표 — 정확도보다 단순성 |
| UTF-8 바이트/4 토큰 추정 | tiktoken | Qwen tokenizer ≠ tiktoken, ±30% 오차 허용 |
| Redis EXPIREAT 월말 설정 | cron 스케줄러 | 별도 프로세스 없이 자동 초기화 |

더 자세한 결정 이유는 [docs/PHASE_TRACKER.md](docs/PHASE_TRACKER.md)를 참고하세요.

---

## 트러블슈팅 하이라이트

**① Python `re` 모듈 유니코드 함정**
```python
# 실패: 한국어 "년"이 \w로 인식되어 \b 경계 미작동
re.compile(r"\b(?:19|20)\d{2}\b").findall("2024년 GDP")  # []

# 해결: re.ASCII 플래그
re.compile(r"\b(?:19|20)\d{2}\b", re.ASCII).findall("2024년 GDP")  # ['2024']
```

**② langdetect 오인식**
```python
detect("파이썬 list와 tuple 차이가 뭐야")  # 'et' (에스토니아어!)
detect(normalize_query("파이썬 list와 tuple 차이가 뭐야"))  # 'ko' ✅
```

**③ Redis KNN 별칭 충돌**
```python
# 실패: Document 기본 .score(=0)와 충돌
Query("(*)=>[KNN 3 @embedding $vec AS score]")

# 해결: 다른 이름 사용
Query("(*)=>[KNN 3 @embedding $vec AS vec_score]")
```

---

## 연관 프로젝트

**[MindGraph-AI](https://github.com/kkdo11/mindgraph-ai)** — LLM-OPT의 실제 소비자.
Spring Boot + LangChain4j 기반 개인형 지식 그래프 AI 에이전트.

```
[MindGraph-AI]
  POST /api/chat (LangChain4j Ollama 포맷)
       │
       ▼
[LLM-OPT :8000]  ← 이 프로젝트
       │
       ▼
[Ollama :11434]
  Qwen 2.5 14B
```

---

## 라이선스

MIT
