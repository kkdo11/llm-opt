# LLM-OPT Phase Tracker

> 이 문서는 프로젝트 진행 상황을 추적하는 살아있는 문서입니다.
> Phase를 진행하면서 Claude에게 이 문서의 업데이트를 요청하거나, 직접 수정하여 재업로드하세요.

---

## 현재 상태: Phase 2 구현 완료 / Threshold 실험 대기

---

## Phase 1 — Baseline 측정 및 Core Proxy 구축

**가설:** 기본 Hash 캐싱만으로도 20% 이상 API 호출 감소 가능

**상태:** ✅ 완료

### 구현 체크리스트

- [x] FastAPI 기반 LLM Proxy 서버
  - [x] OpenAI API 연동 (Ollama 호환, LLM_MODE=mock/ollama)
  - [x] 요청/응답 로깅 (JSON Lines)
  - [x] 기본 에러 핸들링
- [x] Redis Key-Value Cache
  - [x] 질문 → MD5 Hash → Key 매핑
  - [x] TTL 24시간 설정
- [x] 측정 인프라
  - [x] Prometheus exporter (prometheus-fastapi-instrumentator)
  - [x] 메트릭: api_calls_total, cache_hits_total, latency_seconds
- [ ] 실험 실행
  - [ ] 테스트 질문 셋 1,000개 준비 (중복 50% 포함)
  - [ ] Baseline 측정 (캐시 없이)
  - [ ] Cache 적용 측정

### 성공 기준

- [x] Hash Cache Hit Ratio > 20%
- [x] 평균 응답 시간 Baseline 확보
- [x] API 호출 20% 감소

### 실제 결과

```
실측 환경: Qwen 2.5 14B (Ollama, RTX 4080 Super), Redis Stack 7.4
실험 구성: 20개 고유 질문 × 2회 = 총 40 요청 (중복 50%)
측정 일시: 2026-02-21

[실측치]
- Hash Cache Hit Ratio:     50.0%  (목표 >20% ✅)
- Baseline 평균 Latency:    6,065ms  (캐시 미스 = LLM 실제 호출)
- Cache Hit 평균 Latency:   0.3ms
- 속도 향상:                20,217x (캐시 히트 시)
- API 호출 감소율:           50.0%  (목표 >20% ✅)
- 에러율:                   0%

[해석]
- 중복 요청 비율이 정확히 50%이므로 히트율 50%는 설계대로 동작
- LLM 레이턴시 편차 큼 (670ms ~ 11,059ms) — 응답 길이에 의존
- 캐시 히트 레이턴시 0.3ms = Redis 조회 비용만 발생 (사실상 무시 가능)
- 실제 운영에서 중복률이 20~30%만 되어도 목표 달성 가능
```

### 면접 포인트 (Phase 1)

```
Q: 왜 캐시 키로 MD5를 사용했나요?
A: 동일한 메시지 배열을 JSON 직렬화(sort_keys=True)한 후 MD5 해시를 적용.
   128비트 → 32자 hex string으로 Redis 키 길이 고정.
   SHA-256보다 연산 빠르고, 캐시 키 충돌 목적이라 암호학적 안전성 불필요.

Q: 실측 결과 어떻게 나왔나요?
A: 중복 50% 시나리오에서 캐시 히트율 50%, API 호출 50% 감소.
   캐시 미스(LLM) 평균 6,065ms → 캐시 히트 평균 0.3ms, 약 20,000x 속도 향상.

Q: Hash Cache의 한계는?
A: 완전 동일한 문자열만 히트 → "파이썬 리스트 vs 튜플"과 "list tuple 차이"는 미스.
   이를 해결하기 위해 Phase 2에서 Semantic Cache(Vector Search) 추가 예정.
```

### 블로그 소재

```
- Redis 캐시 히트 시 20,000배 속도 차이 실측 사례
- LLM 응답 시간 편차 분석 (670ms ~ 11,059ms, 같은 모델인데 왜 이렇게 다른가)
- Hash Cache의 한계: "같은 의미 다른 표현" 문제 → Phase 2 Semantic Cache 동기
```

---

## Phase 2 — Semantic Cache + Validation Layer

**가설:** Cosine Similarity 0.85 임계값에서 False Positive < 5%, Cache Hit Ratio > 40%

**상태:** ✅ 구현 완료 / Threshold 실험 대기

### 구현 체크리스트

- [x] Embedding 파이프라인
  - [x] SentenceTransformer (all-MiniLM-L6-v2) 통합
  - [x] 임베딩 벡터 생성 및 저장 (asyncio.to_thread로 블로킹 방지)
- [x] Redis Vector Search
  - [x] HNSW Index 생성 (384차원, COSINE, M=16, EF_CONSTRUCTION=200)
  - [x] 유사도 검색 구현 (COSINE distance → similarity = 1.0 - score)
- [x] Validation Layer (핵심)
  - [x] 언어 일치 검사 (langdetect)
  - [x] 숫자/연도/버전 키워드 검증 (re.ASCII 플래그)
  - [x] 기술 키워드 검증 (프로그래밍 언어 불일치 방지)
- [x] L1→L2→LLM 요청 흐름 구현
  - [x] L2 히트 시 L1에도 백필 (다음 동일 요청은 L1에서 처리)
  - [x] 임베딩 1회 계산 후 L2 검색·저장에 재사용
- [x] 단위 테스트 (56/56 통과)
  - [x] test_validator.py: 25개 테스트
  - [x] test_vector_cache.py: 11개 테스트
  - [x] test_proxy.py: 20개 테스트 (SEMANTIC_CACHE_ENABLED=false 격리)
- [ ] Threshold 실험
  - [ ] 0.75 / 0.80 / 0.85 / 0.90 비교 테스트
  - [ ] 최적 Threshold 선정 및 근거 문서화

### 성공 기준

- [ ] Cache Hit Ratio > 40%  ← 미달 (실측 50%이나 내용 오류 포함)
- [x] False Positive Rate < 5%  ← 0.80+ 에서 달성 (하지만 Hit=0%)
- [ ] API 호출 40% 이상 감소  ← 미달

### Threshold 실험 실측 결과 (2026-02-21)

```
모델: paraphrase-multilingual-MiniLM-L12-v2 (384차원)
환경: qwen2.5:14b (Ollama), Redis Stack 7.4 HNSW
질문 쌍: 10개 (expected_hit=True 6개, expected_hit=False 4개)

[실측치]
Threshold | Hit Ratio | False Positive | False Negative | 평가
--------------------------------------------------------------
   0.75   |   50.0%   |     25.0%      |     50.0%      | FP 과다
   0.80   |    0.0%   |      0.0%      |    100.0%      | 히트 불가
   0.85   |    0.0%   |      0.0%      |    100.0%      | 히트 불가
   0.90   |    0.0%   |      0.0%      |    100.0%      | 히트 불가

[결론] 성공 기준(FP<5%, Hit>40%) 동시 달성 불가

[원인 분석 — 3가지]
1. KNN 잘못된 원본 매칭
   - "파이썬 버블소트 코드 짜줘" KNN 1 → "오버피팅이 무엇인지 설명해줘" (sim=0.948)
   - 10개 원본 중 의도한 원본이 아닌 구조 유사 원본과 매칭
   - paraphrase 모델이 "~해줘", "~설명해줘" 형식을 과도하게 유사하게 임베딩

2. threshold=0.75~0.80 사이 절벽
   - Hit@0.75=50%, Hit@0.80=0% → 히트 가능 쌍이 sim 0.75~0.80 범위에 집중
   - 즉 실제 유사도 분포가 threshold 경계값 근처에 몰려있음

3. Validation Layer 미커버 케이스
   - "TCP/UDP 차이점" 캐시에 "HTTP/HTTPS 차이점"이 히트됨
   - 두 질문 모두 네트워크 프로토콜 — PROGRAMMING_LANGUAGES 필터로 구분 불가
   - 프로토콜 키워드(tcp, udp, http, https) 검증 로직 미구현

[향후 개선 방향]
A. KNN k=3 후 컨텐츠 검증 강화 (현재 k=1만 사용)
B. 도메인별 키워드 사전 확장 (프로토콜, 알고리즘, 연도 등)
C. 한국어 특화 임베딩 모델 검토 (예: ko-sroberta-multitask)
```

### 통합 검증 결과 (2026-02-21)

```
모델: paraphrase-multilingual-MiniLM-L12-v2 (384차원)
threshold: 0.85
환경: qwen2.5:14b (Ollama), Redis Stack 7.4

[검증 시나리오]
1차: "파이썬으로 버블 정렬 구현해줘" → cached=False, 7,657ms (LLM 호출)
2차: "파이썬 버블소트 코드 짜줘" (paraphrase) → cached=True, tier=l2_semantic, 25.5ms ✅
3차: "파이썬 버블소트 코드 짜줘" (재요청) → cached=True, tier=l1_hash, 0.5ms ✅ (L1 백필 확인)
4차: "자바로 버블 정렬 구현해줘" (FP 방어) → cached=False, 6,326ms ✅ (Validation Layer 작동)

[핵심 발견: 모델 선택]
초기 설계: all-MiniLM-L6-v2 (영어 최적화)
실측 문제: 한국어 paraphrase 유사도 0.32~0.77 → threshold=0.85 미달
해결: paraphrase-multilingual-MiniLM-L12-v2로 교체
교체 후: 한국어 paraphrase 유사도 0.87~0.95 → threshold=0.85 통과
(단, 두 쌍 "오버피팅이란" 0.57, "Stack과 Queue" 0.25는 여전히 낮음)
```

### 트러블슈팅 기록

```
[2026-02-21] re.ASCII 플래그 이슈
- 문제: \b(19|20)\d{2}\b 패턴이 한국어 텍스트("2024년")에서 미매치
- 원인 1: Python 3 re 모듈은 유니코드 모드가 기본. 한국어 "년"은 \w이므로
          "2024년"에서 "2024" 뒤에 \b 경계가 발생하지 않음
- 원인 2: (19|20) 캡처 그룹으로 인해 findall()이 "20" 반환 (전체 매치 대신 그룹 반환)
- 해결: re.compile(r"\b(?:19|20)\d{2}\b", re.ASCII) — ASCII 모드 + 비캡처 그룹

[2026-02-21] TestClient lifespan 격리 이슈
- 문제: test_health_degraded_without_redis 실패 — lifespan이 _redis_client를 덮어씀
- 원인: TestClient(app) context manager가 lifespan을 실행하므로,
        with 블록 진입 전에 주입한 mock이 lifespan에 의해 덮어씌워짐
- 해결: with TestClient(app) as client: 블록 내부에서 mock 주입
        (lifespan 완료 후 교체). SEMANTIC_CACHE_ENABLED=false로
        SentenceTransformer 로딩 방지
```

### 면접 포인트 (Phase 2)

```
Q: 실험 결과가 목표치를 달성하지 못했는데, 어떻게 분석했나요?
A: 3가지 원인을 발견했습니다.
   (1) KNN k=1 검색에서 의도한 원본이 아닌 구조 유사 원본 반환
       — "파이썬 버블소트"가 "오버피팅" 캐시와 sim=0.948로 매칭
   (2) Validation Layer가 네트워크 프로토콜 키워드를 구분하지 못함
       — TCP/UDP vs HTTP/HTTPS는 PROGRAMMING_LANGUAGES 필터 밖
   (3) 한국어 질문에서 임베딩 벡터가 문장 구조(~해줘, ~설명해줘)에
       과도하게 영향받아 내용 차이를 충분히 반영하지 못함

Q: Semantic Cache에서 COSINE 유사도를 어떻게 계산하나요?
A: Redis Stack HNSW 인덱스는 COSINE distance를 반환 (0=동일, 2=반대).
   이를 similarity = 1.0 - distance로 변환. threshold=0.85 이상일 때만 히트.

Q: False Positive를 왜 Validation Layer로 추가 방어하나요?
A: Cosine 유사도만으로는 의미적 세부 차이를 놓침.
   예: "파이썬 정렬"과 "자바 정렬"은 구조 유사하지만 다른 답변이 필요.
   3단계 검증(언어/숫자키워드/기술키워드)으로 False Positive를 차단.

Q: 임베딩을 왜 asyncio.to_thread로 감쌌나요?
A: SentenceTransformer.encode()는 동기 CPU 연산. FastAPI 이벤트 루프를 블로킹하면
   다른 요청을 처리 못 함. asyncio.to_thread로 스레드 풀에 오프로드.

Q: L2 히트 시 L1에도 저장하는 이유는?
A: 의미 유사 질문이 동일 캐시 응답을 재사용한다면,
   다음에 똑같이 물어볼 확률이 높음. L1에 백필하면 이후 요청은
   임베딩 계산 없이 O(1) Hash 조회로 처리됨 → 레이턴시 추가 감소.
```

### 블로그 소재

```
- Python re 모듈의 \b 경계와 유니코드 함정 (한국어 + ASCII 혼용 시 주의사항)
- FastAPI TestClient + lifespan 테스트 패턴 (mock 주입 타이밍)
- Redis HNSW Vector Search: COSINE distance vs similarity 변환
- 임베딩 1회 계산 재사용 최적화: L2 검색 → LLM 응답 → L2 저장
```

---

## Phase 3 — 입출력 통합 비용 보호

**가설:** 출력 토큰 예측으로 비용 폭탄 사전 차단 가능

**상태:** ⬜ 시작 전

### 구현 체크리스트

- [ ] 출력 토큰 예측 모델
  - [ ] 질문 유형별 패턴 정의
  - [ ] 비용 계산 로직 (입력+출력 통합)
- [ ] Response Streaming 모니터링
  - [ ] 실시간 출력 토큰 카운팅
  - [ ] 예측치 150% 초과 시 중단 로직
- [ ] 사용자별 월간 할당량
  - [ ] Redis 기반 토큰 사용량 추적
  - [ ] 80% 경고, 100% 차단

### 성공 기준

- [ ] 비용 폭탄 차단율 > 95%
- [ ] 평균 요청당 비용 30% 이상 감소

### 실제 결과

```
(Phase 3 완료 후 실측치 기록)
```

### 면접 포인트 (Phase 3)

```
(Phase 3 완료 후 정리)
```

---

## Phase 4 — Kubernetes 배포 및 Adaptive Scaling

**가설:** 이동 평균 기반 HPA로 안정적인 Auto-scaling 가능

**상태:** ⬜ 시작 전

### 구현 체크리스트

- [ ] Custom Metrics Exporter
  - [ ] Queue 길이 이동 평균 계산
  - [ ] Prometheus Gauge 노출
- [ ] HPA 설정
  - [ ] Scale Up: 1분 이동평균 > 20
  - [ ] Scale Down: 5분 이동평균 < 5
  - [ ] 비대칭 정책 (빠른 증가, 느린 감소)
- [ ] Predictive Scaling
  - [ ] 시간대 패턴 정의 (출근, 점심, 퇴근)
  - [ ] multiplier 적용 로직
- [ ] k6 부하 테스트
  - [ ] 정상 / 증가 / 피크 / Spike / Soak 시나리오
  - [ ] 에러율 및 Latency 측정

### 성공 기준

- [ ] 1,000명 동시 접속 시 에러율 < 1%
- [ ] Autoscaling 응답 시간 < 60초
- [ ] P95 Latency < 2초

### 실제 결과

```
(Phase 4 완료 후 실측치 기록)
```

### 면접 포인트 (Phase 4)

```
(Phase 4 완료 후 정리)
```

---

## Phase 5 — Observability 및 비용 시각화

**목표:** 운영 지표를 실시간 비용으로 환산하여 의사결정 지원

**상태:** ⬜ 시작 전

### 구현 체크리스트

- [ ] Prometheus 메트릭 정의
  - [ ] API 호출 (Counter)
  - [ ] Cache 효율 (Counter, 티어별)
  - [ ] 비용 (Counter, actual/saved)
  - [ ] 토큰 사용량 (Counter, input/output)
  - [ ] 레이턴시 (Histogram, cache_status별)
- [ ] 실시간 비용 계산
  - [ ] 실제 비용 계산
  - [ ] 절감 비용 계산
  - [ ] 월간 비용 예측
- [ ] Grafana 대시보드
  - [ ] 패널 1: 실시간 비용 누적
  - [ ] 패널 2: 캐시 효율 분석
  - [ ] 패널 3: 비용 비교 (캐시 유/무)
  - [ ] 패널 4: 레이턴시 분포
  - [ ] 패널 5: Pod Autoscaling

### 성공 기준

- [ ] 대시보드에서 실시간 비용 추적 가능
- [ ] Cache Hit Ratio 시각화
- [ ] 월간 비용 예측 정확도 ±10% 이내

### 실제 결과

```
(Phase 5 완료 후 실측치 기록)
```

### 면접 포인트 (Phase 5)

```
(Phase 5 완료 후 정리)
```

---

## 누적 의사결정 로그

> Phase를 진행하면서 내린 기술적 의사결정을 여기에 기록합니다.
> 면접에서 "왜 이렇게 했나요?" 질문에 대한 근거로 활용합니다.

| 날짜 | Phase | 결정 사항 | 대안 | 선택 근거 |
|------|-------|-----------|------|-----------|
| 2026-02-21 | 1 | redis-stack 사용 (Phase 1부터) | redis 공식 이미지 | Phase 2 Vector Search 준비; 컨테이너 교체 비용 최소화 |
| 2026-02-21 | 1 | LLM_MODE=mock 개발 환경 분리 | 실제 Ollama 연결 | GPU 없이 개발/테스트 가능; 측정값은 실 Ollama 연결 후 기록 |
| 2026-02-21 | 1 | prometheus-fastapi-instrumentator | 직접 /metrics 구현 | 라우터 레벨 자동 계측으로 코드 중복 제거; FastAPI 통합 검증됨 |
| 2026-02-21 | 2 | paraphrase-multilingual-MiniLM-L12-v2 선택 (교체) | all-MiniLM-L6-v2 (영어 전용) | 한국어 paraphrase 실측 유사도 0.32→0.87 향상; 384차원 동일하여 인덱스 재생성 불필요 |
| 2026-02-21 | 2 | Validation Layer 3단계 순서 (언어→숫자→기술) | 단일 threshold만 사용 | 언어 불일치가 가장 빠른 조기 종료; 숫자/버전은 정보 손실이 크므로 우선 차단 |
| 2026-02-21 | 2 | re.ASCII 플래그 적용 | 유니코드 모드 유지 | 한국어 텍스트에서 \b 경계 오작동 방지; 연도/버전은 ASCII 숫자이므로 ASCII 모드로 충분 |

---

## 트러블슈팅 히스토리

> 발생한 문제와 해결 과정을 기록합니다. 블로그 포스트 소재로 활용합니다.

| 날짜 | Phase | 문제 | 원인 | 해결 | 블로그 작성 여부 |
|------|-------|------|------|------|-----------------|
| 2026-02-21 | 2 | `\b` 경계 한국어 미매치 | Python re 유니코드 모드 + 캡처 그룹 | `re.ASCII` 플래그 + 비캡처 그룹 `(?:...)` | ⬜ |
| 2026-02-21 | 2 | TestClient lifespan mock 격리 실패 | lifespan이 with 블록 진입 전 mock 덮어씀 | with 블록 내부에서 mock 주입, `SEMANTIC_CACHE_ENABLED=false` | ⬜ |
| 2026-02-21 | 2 | redis-py `AS score` KNN 별칭이 기본 score 속성과 충돌 | FT.SEARCH Document의 `.score`는 기본 relevance score(0) | KNN 별칭을 `vec_score`로 변경, `getattr(doc, "vec_score")` 접근 | ⬜ |
| 2026-02-21 | 2 | all-MiniLM-L6-v2 한국어 paraphrase 유사도 낮음 | 영어 최적화 모델 — 한국어 paraphrase 0.32~0.77 | `paraphrase-multilingual-MiniLM-L12-v2`로 교체 → 0.87~0.95 | ⬜ |
