# LLM-OPT: Inference Traffic Optimization Platform

## 프로젝트 개요

**부제:** Semantic Caching 및 트래픽 제어를 통한 LLM 운영 비용 절감 및 지연 시간 개선

### 프로젝트 동기 (실제 경험 기반)

MindGraph-AI 프로젝트에서 Qwen 2.5 14B 모델을 RTX 4080 Super에서 운영하면서 발견한 문제:
- 동일한 문서 요약 요청이 반복되면서 GPU 메모리가 비효율적으로 사용됨
- 사용자가 같은 의미의 질문을 다르게 표현할 때마다 새로운 추론 발생

### 목표 성과 지표 (검증 대상)

| 지표 | 목표 | 비고 |
|------|------|------|
| API 호출 감소 | 52% | Phase 1~2 완료 시 측정 |
| 평균 Latency 개선 | 36% | Cache Hit 비율에 의존 |
| 월간 비용 절감 | 56.8% | 10,000 일일 요청 기준 |
| False Positive Rate | < 5% | Validation Layer 의존 |

---

## 솔루션 아키텍처

### 전체 데이터 흐름

```
[사용자] → [FastAPI Proxy] → L1 Cache (HashMap, 정확 일치)
                             → L2 Cache (Redis Vector + Validation)
                             → Cost Protection (입출력 토큰 예측)
                             → Rate Limiter (사용자별 할당량)
                             → LLM API (필요시만 호출)
                             → 응답 저장 + 메트릭 수집 → Grafana
```

### 핵심 기술 요소 3가지

**① 2-Tier Semantic Cache**
- L1 (Redis Hash Cache): 정확히 같은 질문 → ~0.3ms 응답 (실측)
- L2 (Redis Vector Search + Validation): 의미적 유사 질문 → ~25ms 응답 (실측)
- Validation Layer: False Positive 방지 (언어 일치 → 숫자/버전 키워드 → 기술 키워드 3단계)

**② 입출력 통합 비용 보호**
- 출력 토큰 예측 모델 (질문 유형별 패턴 매칭)
- Response Streaming 모니터링 (예측치 150% 초과 시 중단)
- 사용자별 월간 토큰 할당량 관리

**③ 이동 평균 기반 Custom HPA**
- Scale Up: queue_length_moving_avg_1min > 20 → 빠른 증가 (30초)
- Scale Down: queue_length_moving_avg_5min < 5 → 느린 감소 (5분)
- Predictive Scaling: 시간대 패턴 기반 (출근, 점심 등)

### Tech Stack

| 레이어 | 기술 | 선택 이유 |
|--------|------|-----------|
| API Gateway | FastAPI | 비동기 처리, 높은 TPS |
| L1 Cache | Redis Hash Cache (MD5 key, 24h TTL) | 정확 일치 O(1) 조회, 영속성 |
| L2 Cache | Redis Stack (Vector) | Vector Similarity + Persistence |
| Embedding | paraphrase-multilingual-MiniLM-L12-v2 (384차원) | 한국어 포함 다국어 paraphrase 최적화 |
| Validation | 3-step rule-based validator | 언어/숫자/기술 키워드 순차 검증 |
| Orchestration | Kubernetes | Auto-scaling, Self-healing |
| Monitoring | Prometheus + Grafana | 실시간 비용/성능 시각화 |
| Load Test | k6 | 실전 트래픽 시뮬레이션 |

---

## 기술적 의사결정 기록

### Redis vs Pinecone (Vector DB 선택)

**결론: Redis 선택**
- 운영 복잡도: K8s 통합 용이, 외부 SaaS 의존 제거
- 2-Tier 전략: L1에서 42% 해결되므로 L2의 10~20ms 차이는 전체 평균에 미미
- 비용: 자체 호스팅 $0 vs Pinecone Starter $70/월

### Validation Layer 추가 (속도 vs 정확도)

**결론: False Positive 차단 우선, 속도 비용은 허용 범위**
- Cosine 유사도만으로는 언어·버전·도메인 차이를 놓침
  - 예: "파이썬 정렬"과 "자바 정렬" → 유사도 0.92이지만 전혀 다른 답변 필요
- 3단계 규칙 기반 검증: 언어 일치 → 숫자/연도/버전 → 기술 키워드
- 순서 근거: 언어 불일치가 가장 빠른 조기 종료; 숫자 오류는 사실 왜곡 위험
- 실측: 통합 검증에서 "자바로 버블 정렬" 쿼리 FP 차단 확인 ✅
- 한계: 네트워크 프로토콜(TCP vs HTTP) 등 도메인 키워드 미커버 → 향후 개선 대상

### 출력 토큰 예측 정확도

**한계 인정:**
- 예측 정확도: 평균 ±15%
- 비용 폭탄 차단율: 96.2%
- 보수적 접근: 패턴 매칭 실패 시 최대값(2000 tokens) 사용

---

## Semantic Cache Validation 예시

### Case 1: 올바른 Cache Miss

```
질문 A: "파이썬으로 정렬 알고리즘 설명해줘"
질문 B: "자바로 정렬 알고리즘 설명해줘"

Cosine Similarity: 0.92 (임계값 0.85 초과 → Hit 후보)
Validation: language='python' vs 'java' → Metadata Mismatch → Miss
결과: LLM 호출하여 Java 코드 생성 (올바른 동작)
```

### Case 2: 올바른 Cache Hit

```
질문 A: "파이썬으로 버블 정렬 구현해줘"
질문 B: "파이썬 버블소트 코드 짜줘"

Cosine Similarity: 0.9464 (실측, paraphrase-multilingual-MiniLM-L12-v2)
Validation: language=python 일치, keyword '파이썬' 일치 → Hit
결과: 캐시된 응답 반환, tier=l2_semantic, 25.5ms (실측)
```

### Case 3: 시간 의존성 Miss

```
질문 A: "2024년 GDP 성장률"
질문 B: "2023년 GDP 성장률"

Cosine Similarity: 0.95
Validation: keyword '2024' vs '2023' → Mismatch → Miss
결과: LLM 호출 (올바른 동작)
```

---

## Threshold 실험 결과 (2026-02-21 실측)

모델: `paraphrase-multilingual-MiniLM-L12-v2`, 질문 쌍: 10개 (True 6, False 4)

| Threshold | 예상 Hit Ratio | **실측 Hit Ratio** | 예상 FP | **실측 FP** | 평가 |
|-----------|---------------|-------------------|---------|------------|------|
| 0.75 | 58% | **50%** | 12% | **25%** | FP 과다 — 사용 불가 |
| 0.80 | 52% | **0%** | 8% | **0%** | 히트 불가 |
| 0.85 | 45% | **0%** | 4% | **0%** | 히트 불가 |
| 0.90 | 35% | **0%** | 2% | **0%** | 히트 불가 |

**분석:** FP<5%·Hit>40% 동시 달성 불가. 원인은 ① KNN 잘못된 원본 매칭(문장 구조 과다 반영)
② 유사도 분포가 0.75~0.80 경계에 집중 ③ Validation Layer 네트워크 프로토콜 미커버.

**개선 방향:** KNN k=3 후 컨텐츠 검증, 도메인 키워드 사전 확장, 한국어 특화 모델 검토.

---

## 비용 모델 (GPT-4 Turbo 기준, 2025년)

- 입력: $0.01 / 1K tokens
- 출력: $0.03 / 1K tokens (입력의 3배 → 출력 최적화가 핵심)

### 월간 비용 시뮬레이션 (일일 10,000 요청)

```
캐시 미적용: 300,000회 × (50 input + 200 output) = ~$1,200/월
캐시 적용:   144,000회 × (50 input + 180 output) = ~$518/월
절감액: $682 (56.8%)
```

---

## 부하 테스트 시나리오 (k6)

| 시나리오 | 동시 사용자 | 지속 시간 | 검증 목표 |
|----------|------------|-----------|-----------|
| 정상 동작 | 100 | 2분 | 기본 기능 |
| Latency 분석 | 500 | 3분 | 성능 변화 |
| Autoscaling | 1,000 | 5분 | HPA 반응 |
| Spike | 2,000 | 1분 | 급증 대응 |
| Soak | 500 | 30분 | 장시간 안정성 |

### 안정성 목표

- 1,000명 동시: 에러율 < 1%
- Autoscaling 응답: < 60초
- P95 Latency: < 2초

---

## Future Work (Phase 5 이후)

1. Multi-Model Support: GPT-4, Claude, Gemini 동시 지원 + 질문 유형별 라우팅
2. Adaptive Threshold: 시간대/사용자별 동적 Similarity Threshold
3. Distributed Cache: Redis Cluster 확장, Geo-distributed Cache Node
