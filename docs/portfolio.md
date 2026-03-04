# LLM-OPT: LLM 인퍼런스 트래픽 최적화 프록시

> 로컬 LLM의 반복 추론 비용을 줄이는 2계층 시맨틱 캐시 프록시.
> 동일 질문 응답 **6,065ms → 0.3ms**, 유사 의미 질문 **6,065ms → 25.5ms**.

---

## 배경 — 왜 만들었나

개인 지식 그래프 시스템(MindGraph-AI)을 운영하던 중 이상한 점을 발견했다.

"Docker란 무엇인가?"를 반복해서 물어볼 때마다 Qwen 2.5 14B가 **매번 6초씩** 동일한 내용을 다시 생성하고 있었다. RTX 4080 Super(16GB VRAM) 단일 머신에서 GPU가 이미 알고 있는 답변을 반복 추론하는 건 명백한 낭비였다.

문제는 두 가지였다.

1. **완전히 동일한 질문** — 캐싱으로 즉시 해결 가능한데 하지 않고 있음
2. **의미가 같지만 표현이 다른 질문** — "파이썬으로 버블 정렬 구현해줘"와 "파이썬 버블소트 코드 짜줘"는 같은 답변이 나와야 하는데 두 번 LLM을 호출함

이 두 문제를 해결하기 위해 LLM-OPT를 설계했다.

---

## 접근 방식 — 2계층 캐시

단일 캐시 레이어로는 두 문제를 동시에 해결할 수 없다. 정확한 매칭과 의미적 매칭은 서로 다른 메커니즘이 필요하다.

```
요청
 │
 ├── L1: MD5 Hash Cache (Redis)
 │     완전히 동일한 문자열만 히트
 │     비용: 0.3ms (Redis 조회만)
 │
 ├── L2: Semantic Cache (Redis HNSW)
 │     의미적으로 유사한 요청 히트
 │     비용: 25.5ms (임베딩 생성 + 벡터 검색 + Validation)
 │
 └── LLM (Ollama)
       캐시 미스 시만 실제 추론
       비용: 평균 6,065ms
```

**L1을 먼저 두는 이유**: 임베딩 생성 자체가 수십 ms 비용이다. 완전히 동일한 요청이라면 MD5 해시 하나로 0.3ms에 처리할 수 있다. 정확한 매칭 먼저, 의미적 매칭은 그 다음.

**L2 히트 시 L1 백필**: 의미적으로 유사한 요청이 L2에서 히트되면, 해당 답변을 L1에도 저장한다. 이후 동일 표현이 오면 L1에서 0.3ms로 처리된다.

---

## 구현 — 핵심 3가지

### 1. Validation Layer — False Positive 방지

시맨틱 캐시의 핵심 과제는 False Positive다. 코사인 유사도가 높다고 해서 같은 답변을 돌려줘도 되는 건 아니다.

초기 실험에서 실제 발생한 문제들:

```
"TCP/UDP 차이점 설명해줘"
    → 캐시에서 "HTTP/HTTPS 차이점 설명해줘" 히트 (cosine 0.82)
    → 잘못된 응답 반환 ← False Positive

"Python 3.10 릴리즈 노트"
    → 캐시에서 "Python 3.11 릴리즈 노트" 히트 (cosine 0.91)
    → 버전이 다른데 같은 응답 반환 ← False Positive
```

이를 막기 위해 3단계 Validation Layer를 직접 설계했다.

```python
# 1단계: 언어 일치 검사
# "What is Docker?" → 한국어 답변 캐시에 히트되면 차단
if query_lang != cached_lang:
    return False  # 언어 불일치

# 2단계: 숫자/연도/버전 검증
# "Python 3.10"과 "Python 3.11"은 같은 답변을 줄 수 없다
query_numbers = extract_numbers(query)      # {"3.10"}
cached_numbers = extract_numbers(cached)   # {"3.11"}
if query_numbers != cached_numbers:
    return False  # 숫자 불일치

# 3단계: 기술 키워드 검증 (도메인 사전)
# PROGRAMMING_LANGUAGES, NETWORK_PROTOCOLS, ...
# "TCP"가 쿼리에 있는데 캐시에 "HTTP"만 있으면 차단
```

**최종 결과: False Positive Rate 0%** (실측)

---

### 2. threshold 실험 — 데이터로 결정

임계값(threshold)은 직관이 아닌 실험으로 결정했다.

```
실험 설계: 10개 질문 쌍 (유사 6쌍, 다른 의미 4쌍)
비교 값: 0.75 / 0.80 / 0.85 / 0.90

[1차 실험 결과 — 2026-02-21]
Threshold | Hit Rate | False Positive
  0.75   |  50.0%  |    25.0%     ← FP 과다
  0.80   |   0.0%  |     0.0%     ← 히트 불가
  0.85   |   0.0%  |     0.0%     ← 히트 불가
  0.90   |   0.0%  |     0.0%     ← 히트 불가
```

목표(Hit > 40%, FP < 5%)를 동시에 달성하는 값이 없었다. 단순히 값을 조정하는 게 아니라 **왜** 그런지 원인을 분석했다.

**발견한 3가지 원인:**

1. `KNN k=1` — 의도한 원본이 아닌 구조적으로 비슷한 다른 원본과 매칭
2. `langdetect 오인식` — "파이썬 list와 tuple 차이가 뭐야"를 에스토니아어로 판정해서 언어 불일치로 FAIL
3. `프로토콜 키워드 미검증` — TCP/UDP vs HTTP/HTTPS 구분 불가

각 원인을 보완한 후 재실험했다:

```
[2차 실험 결과 — 2026-02-22, 보완 후]
항목          | 보완 전  | 보완 후  | 변화
Hit Rate     | 50.0%  | 66.7%  | +16.7% ✅
FP Rate      |  0.0%  |  0.0%  | 유지  ✅

→ threshold=0.75, Hit 66.7%, FP 0% 달성
```

---

### 3. 쿼리 정규화 — 임베딩 품질 개선

한국어와 영어 기술 용어를 혼용하면 임베딩 품질이 떨어진다.

```
"파이썬 list와 tuple 차이가 뭐야"
 → langdetect: 'et' (에스토니아어 오인식!)
 → 정규화 전 유사도: 0.87
```

정규화 레이어(normalizer.py)를 추가해 임베딩 전에 기술 용어를 통일했다.

```
"파이썬 list와 tuple 차이가 뭐야"
 → "python list와 tuple 차이가 뭐야" (정규화)
 → langdetect: 'ko' (정상 인식)
 → 정규화 후 유사도: 0.94 → L2 히트 성공
```

---

## 결과

| 항목 | 수치 |
|------|------|
| L1 응답 시간 | **0.3ms** (Redis 조회만) |
| L2 응답 시간 | **25.5ms** (HNSW 검색 + Validation) |
| Baseline LLM | 6,065ms (실측, Qwen 2.5 14B) |
| L1 속도 향상 | **20,217배** |
| L2 속도 향상 | **238배** |
| L1 Hit Rate | 50% (중복 50% 시나리오) |
| L2 Hit Rate | **66.7%** (보완 후) |
| False Positive | **0%** |
| 단위 테스트 | 151개 (인프라 없이 실행) |
| Grafana 대시보드 | 5패널 (비용/캐시/레이턴시/HPA 실시간 시각화) |
| MindGraph 실 연동 | L1 히트 0.43ms 실측 (LLM 5,510ms 대비 12,814x) |

---

## 배운 것

**"측정하고 나서 결정한다"**

threshold 값을 0.80으로 정했다면 실제로 캐시가 한 번도 히트되지 않는 시스템이 만들어졌을 것이다. 임계값 하나가 시스템 전체의 유효성을 좌우했고, 그 값은 직관이 아니라 실험 데이터에서 나왔다.

**"실패의 원인을 분석해야 개선이 가능하다"**

1차 실험 실패 후 "threshold를 낮추면 어떨까?"가 아니라 "왜 실패했나?"를 먼저 물었다. 세 가지 원인을 각각 고쳤고, 결과가 의미있게 바뀌었다.

**"캐시는 속도만이 아니다 — 정확도가 먼저다"**

Semantic Cache에서 FP를 허용하면 사용자가 틀린 답변을 받는다. 빠른 잘못된 답변보다 느린 정확한 답변이 낫다. Validation Layer는 속도를 다소 희생하고 정확도를 지키는 선택이었다.

---

## 기술 스택

| 영역 | 선택 | 이유 |
|------|------|------|
| 프레임워크 | FastAPI | 비동기 처리, OpenAI SDK 호환 엔드포인트 |
| L1 캐시 | Redis Hash | O(1) 조회, 0.3ms 목표 달성 |
| L2 캐시 | Redis HNSW | 벡터 DB 없이 Redis 단일 인스턴스로 구현 가능 |
| 임베딩 모델 | paraphrase-multilingual-MiniLM-L12-v2 | 한국어 포함 다국어 지원, 384차원 (속도/품질 균형) |
| LLM | Qwen 2.5 14B (Ollama) | 로컬 서빙, 클라우드 API 비용 없음 |
| 모니터링 | Prometheus + Grafana 11.4.0 | 비용·캐시·레이턴시·HPA 5패널 실시간 대시보드 |
| 멀티 백엔드 | LLMBackend ABC (Ollama/OpenAI) | 환경변수 DI, MindGraph 실 연동 |
| 테스트 | pytest + pytest-asyncio | 외부 의존성 전체 Mock, 151개 통과 |

---

## 코드 하이라이트

**L1 → L2 → LLM 흐름 (main.py)**

```python
async def process_request(request: ChatRequest) -> ChatResponse:
    # L1: Hash Cache
    cache_key = compute_hash(request.messages)
    if cached := await redis_cache.get(cache_key):
        return ChatResponse(content=cached, cached=True, tier="l1_hash")

    # L2: Semantic Cache (임베딩 1회 계산 → 검색·저장 재사용)
    embedding = await compute_embedding(request.messages)
    if result := await vector_cache.search(embedding):
        if validator.validate(request.messages, result.query):
            await redis_cache.set(cache_key, result.content)  # L1 백필
            return ChatResponse(content=result.content, cached=True, tier="l2_semantic")

    # LLM 호출
    response = await llm_backend.chat(request.messages)
    await vector_cache.store(embedding, request.messages, response.content)
    await redis_cache.set(cache_key, response.content)
    return ChatResponse(content=response.content, cached=False, tier="llm")
```

**Validation Layer 핵심 로직**

```python
def validate(self, query: list[dict], cached_query: list[dict]) -> bool:
    q_text = extract_text(query)
    c_text = extract_text(cached_query)

    # 1. 언어 일치
    if detect_lang(q_text) != detect_lang(c_text):
        return False

    # 2. 숫자/버전 일치 (3.10 ≠ 3.11)
    if extract_numbers(q_text) != extract_numbers(c_text):
        return False

    # 3. 기술 키워드 일치 (TCP ≠ HTTP)
    for domain_keywords in DOMAIN_KEYWORD_SETS:
        q_hits = domain_keywords & set(q_text.lower().split())
        c_hits = domain_keywords & set(c_text.lower().split())
        if q_hits and c_hits and q_hits != c_hits:
            return False

    return True
```
