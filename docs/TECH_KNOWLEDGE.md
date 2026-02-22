# 기술 지식 정리

> 이 프로젝트에서 직접 구현하고 실측하면서 쌓인 기술 지식.
> "들어본 적 있다"가 아니라 "직접 써봤다" 수준의 것들만 기록.

---

## 1. Redis

### 1-1. 데이터 구조 선택

| 용도 | 자료구조 | 키 형태 | 이유 |
|------|----------|---------|------|
| L1 Hash Cache | String | `llm:cache:{md5}` | 단순 key-value, GET/SET O(1) |
| L2 Vector Index | Hash + HNSW | `llm:vec:{uuid}` | RediSearch가 Hash 필드를 인덱싱 |
| 월간 토큰 할당량 | String (숫자) | `llm:quota:{user}:{YYYYMM}` | INCRBY 원자 연산 |

### 1-2. 캐시 키 설계

```python
def cache_key(messages: list[dict]) -> str:
    serialized = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    md5_hash = hashlib.md5(serialized.encode("utf-8")).hexdigest()
    return f"llm:cache:{md5_hash}"
```

- `sort_keys=True`: JSON 직렬화 시 키 순서 고정 → 같은 메시지가 항상 같은 해시
- `ensure_ascii=False`: 한국어를 이스케이프 없이 직렬화 (공간 절약, 가독성)
- MD5: 암호학적 안전성 불필요, 키 길이 32자 고정이 목적

### 1-3. TTL 설정

```python
await client.set(key, value.encode("utf-8"), ex=86400)   # 24시간
await client.expireat(key, month_end_timestamp)            # 월말 만료
```

- `ex=`: 상대적 만료 (N초 후)
- `expireat`: 절대적 만료 (특정 Unix timestamp) → 월간 할당량 초기화에 적합

### 1-4. INCRBY 원자성

```python
total = await redis.incrby(key, tokens)
```

여러 요청이 동시에 토큰을 차감해도 race condition이 없다. Redis는 단일 스레드로 커맨드를 처리하므로 INCRBY는 원자적이다.

---

## 2. Redis Vector Search (HNSW)

### 2-1. 인덱스 생성

```python
await redis.ft("llm:vec:idx").create_index(
    fields=[
        VectorField("embedding",
            algorithm="HNSW",
            attributes={
                "TYPE": "FLOAT32",
                "DIM": 384,
                "DISTANCE_METRIC": "COSINE",
                "M": 16,
                "EF_CONSTRUCTION": 200,
            }
        ),
        TextField("content"),
        TextField("lang"),
        TagField("keywords"),
    ]
)
```

**파라미터 의미:**
- `DIM: 384`: SentenceTransformer 출력 차원 수
- `DISTANCE_METRIC: COSINE`: 코사인 거리 (방향 기반, 벡터 크기 무관)
- `M: 16`: HNSW 그래프에서 각 노드가 연결하는 이웃 수. 높을수록 정확하지만 메모리↑
- `EF_CONSTRUCTION: 200`: 인덱스 구축 시 탐색 범위. 높을수록 정확한 인덱스, 구축 시간↑

### 2-2. COSINE distance → similarity 변환

Redis HNSW는 **거리(distance)**를 반환한다.

```
COSINE distance: 0 = 동일, 2 = 반대
similarity = 1.0 - distance
```

```python
similarity = 1.0 - float(doc.vec_score)
if similarity >= threshold:
    # 캐시 히트
```

주의: KNN 결과의 score 별칭을 `vec_score`로 지정해야 한다.
기본 `.score`는 RediSearch의 text relevance score(0)와 충돌한다.

### 2-3. KNN k=3 전략

```
KNN k=3 결과 → similarity 내림차순 → Validation 통과 첫 번째 선택
```

k=1이면 구조가 유사한 다른 원본과 잘못 매칭될 수 있다.
k=3으로 후보를 늘리고 Validation이 실제 적합한 것을 선택한다.

---

## 3. 임베딩 (Sentence Transformers)

### 3-1. 동작 원리

텍스트 → 토크나이저 → Transformer 인코더 → Pooling → 384차원 벡터

의미적으로 유사한 문장은 벡터 공간에서 가깝다.
코사인 유사도로 거리를 측정한다.

### 3-2. 모델 선택 기준

| 모델 | 언어 | 한국어 paraphrase 실측 |
|------|------|----------------------|
| all-MiniLM-L6-v2 | 영어 위주 | 0.32~0.77 (threshold 미달) |
| paraphrase-multilingual-MiniLM-L12-v2 | 50개 언어 | 0.87~0.95 ✅ |

**교훈**: 모델은 학습 언어에 강하게 의존한다. 직접 측정 전에는 모른다.

### 3-3. normalize_embeddings=True

```python
embedding = model.encode(text, normalize_embeddings=True)
```

벡터 크기를 1로 정규화한다. 코사인 유사도 계산 시 크기 차이의 영향을 제거한다.
Redis HNSW COSINE metric 사용 시 일관성을 위해 필수.

### 3-4. asyncio.to_thread()

```python
embedding = await asyncio.to_thread(model.encode, text, normalize_embeddings=True)
```

`encode()`는 CPU 연산이다. FastAPI 이벤트 루프에서 직접 호출하면 루프가 블로킹된다.
`to_thread()`는 스레드 풀에 오프로드하고 루프는 다른 요청을 계속 처리한다.

---

## 4. FastAPI

### 4-1. lifespan 패턴

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # 앱 시작: DB 연결, 모델 로딩
    _redis = await from_url(...)
    _model = await asyncio.to_thread(SentenceTransformer, model_name)
    yield
    # 앱 종료: 연결 해제
    await _redis.aclose()

app = FastAPI(lifespan=lifespan)
```

`@app.on_event("startup")`의 현대적 대체. yield 전/후로 시작/종료 로직을 한 곳에 관리.

### 4-2. StreamingResponse + SSE

```python
async def _stream_generator():
    yield f"data: {json.dumps({'content': chunk})}\n\n"

return StreamingResponse(_stream_generator(), media_type="text/event-stream")
```

SSE(Server-Sent Events) 형식: `data: {...}\n\n`
OpenAI API 스트리밍과 호환되는 단방향 스트림.

`response_model=None` 필요:
```python
@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(...) -> ChatResponse | StreamingResponse:
```
FastAPI는 `StreamingResponse`를 Pydantic 모델로 검증할 수 없으므로 추론을 비활성화해야 한다.

### 4-3. 환경변수 패턴

```python
COST_PER_INPUT_1K = float(os.getenv("COST_PER_INPUT_1K", "0.0005"))
```

기본값을 코드에 두되 환경변수로 오버라이드. 운영 환경에서 재배포 없이 변경 가능.

---

## 5. 언어 처리

### 5-1. langdetect 한계

langdetect는 문자 n-gram 통계 기반이다.
한국어 + 영어 기술 용어 혼용 문장에서 오인식이 발생한다.

```python
detect("파이썬 list와 tuple 차이가 뭐야")  # → 'et' (에스토니아어 오인식)
```

원인: `list`, `tuple`이 에스토니아어 어휘와 n-gram 패턴이 겹침.

해결: 임베딩 전에 영어 기술 용어를 한국어로 치환 후 감지.

### 5-2. 정규화 (normalizer.py)

```python
_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in _sorted_terms) + r")\b",
    re.IGNORECASE | re.ASCII,
)
def normalize_query(text): return _PATTERN.sub(lambda m: TECH_TERM_MAP[m.group(0).lower()], text)
```

**`re.ASCII` 가 필요한 이유:**
Python 3 기본 유니코드 모드에서 `\b`는 `\w`/`\W` 경계다.
한국어 "년"도 `\w`로 인식 → "2024년"에서 "2024" 뒤에 경계가 생기지 않음 → 미매치.
`re.ASCII` 모드에서는 한국어가 `\W` → 경계 정상 작동.

**단일 alternation 패턴이 루프보다 효율적인 이유:**
루프: N개 패턴을 N번 순회하며 각각 sub() 호출.
단일 패턴: 정규식 엔진이 한 번의 스캔으로 모든 용어를 동시에 매칭.

---

## 6. Validation Layer 설계

### 6-1. 단락 평가 (Short-circuit Evaluation)

```python
# 1단계 실패 → 2, 3단계 실행 안 함
result = validate_language(...)
if not result.passed: return result

result = validate_numeric_keywords(...)
if not result.passed: return result

result = validate_tech_keywords(...)
```

가장 저렴한 검증을 먼저 배치한다.
언어 불일치는 문자열 비교(O(1)), 기술 키워드는 집합 교집합(O(n)).

### 6-2. False Positive > False Negative 원칙

잘못된 응답 반환(FP) > 느린 정확한 응답(FN)

FP: 사용자가 잘못된 정보를 받음 → 서비스 신뢰 손상
FN: LLM을 한 번 더 호출 → 느리지만 정확

불확실하면 항상 캐시 미스로 처리한다.

### 6-3. 키워드 저장 구조

```
저장 시: extract_keywords(query) → ["python", "2024", "v3.11"] → metadata["keywords"]
검색 시: validate_tech_keywords(query_text, cached_metadata["keywords"])
```

프로그래밍 언어, 네트워크 프로토콜, 연도, 버전을 같은 `keywords` 필드에 통합 저장.
별도 필드로 분리하지 않은 이유: Validation이 키워드 존재 여부만 판단하면 충분.

---

## 7. 토큰 예측 & 비용 계산

### 7-1. UTF-8 바이트 / 4 추정

```python
def estimate_tokens(text: str) -> int:
    return max(1, len(text.encode("utf-8")) // 4)
```

tiktoken을 쓰지 않은 이유:
- Qwen2.5는 자체 tokenizer, tiktoken과 토큰 경계가 다름
- ±30% 오차가 있지만, 150% 임계값 기준으로 충분

한국어: UTF-8에서 3bytes/char → "안녕" = 6bytes → 6//4 = 1 token (실제 약 2~3)
영어: 1byte/char → "hello" = 5bytes → 5//4 = 1 token (실제 약 1~2)

### 7-2. Rule-based 질문 유형 분류

```python
VERY_LONG = 1200  # "단계별", "튜토리얼"
LONG      = 800   # "구현해줘", "코드"
MEDIUM    = 400   # "설명해줘", "차이점"
SHORT     = 150   # 기타
```

ML 모델 없이 키워드 패턴으로 충분한 이유:
목표가 "정확한 예측"이 아니라 "150% 초과 감지"이기 때문.
±30% 오차 모델로 150% 임계값을 잡는 건 충분한 안전 마진이다.

### 7-3. 비용 계산

```python
cost = (input_tokens / 1000) * 0.0005 + (output_tokens / 1000) * 0.0015
```

OpenAI GPT-3.5 기준 단가를 기본값으로 사용.
캐시 히트 시 cost_usd = 0.0 (LLM 추론 비용 없음).

---

## 8. 테스트

### 8-1. pytest-asyncio + AsyncMock

```python
@pytest.mark.asyncio
async def test_something():
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=b"100")
    tracker = QuotaTracker(redis=mock_redis, quota=1000)
    status = await tracker.check("user1")
    assert status == QuotaStatus.OK
```

외부 의존성(Redis, LLM)을 `AsyncMock`으로 격리.
`AsyncMock`은 `await`를 지원하는 mock이다.

### 8-2. 전역 상태 격리

FastAPI는 전역 변수로 상태를 관리한다. `TestClient(app)`은 lifespan을 실행하므로 테스트 간 전역 상태가 오염된다.

```python
@pytest.fixture
def mock_cache():
    import src.proxy.main as proxy_main
    proxy_main._quota_tracker = None  # 이전 lifespan 잔류 격리
    cache = MagicMock(spec=RedisCache)
    ...
```

새 전역 변수를 추가할 때마다 fixture에도 초기화 코드를 추가해야 한다.

### 8-3. lifespan 테스트 타이밍

```python
with TestClient(app) as client:          # lifespan 실행
    proxy_main._redis_client = mock      # lifespan 완료 후 mock 주입
    response = client.get("/health")
```

`with` 블록 진입 전에 mock을 주입하면 lifespan이 덮어쓴다.
반드시 `with` 블록 내부에서 mock을 교체해야 한다.

---

## 9. Prometheus 메트릭

```python
from prometheus_fastapi_instrumentator import Instrumentator
Instrumentator().instrument(app).expose(app)

# 커스텀 메트릭
cache_hits_total = Counter("cache_hits_total", "캐시 히트 수", ["tier"])
latency_seconds  = Histogram("latency_seconds", "요청 레이턴시", ["cache_status"])

cache_hits_total.labels(tier="l1_hash").inc()
latency_seconds.labels(cache_status="hit").observe(elapsed / 1000)
```

**Counter**: 단조 증가 (히트 수, API 호출 수)
**Histogram**: 분포 측정 (레이턴시) → p50, p95, p99 계산 가능
**labels**: 차원 추가 (tier별, cache_status별 분리 집계)

---

## 10. 아키텍처 패턴

### L1 → L2 → LLM 계층 구조

```
요청
 ├─ L1 Hash Cache (O(1), 0.3ms)
 │    HIT → 즉시 반환
 ├─ L2 Semantic Cache (O(log n), 25ms)
 │    HIT → L1에도 백필 → 반환
 └─ LLM 호출 (6,000ms~)
      → L1 + L2 모두 저장
```

**L2 히트 시 L1에도 백필하는 이유:**
의미 유사 질문이 캐시를 재사용했다면 다음에 똑같이 물어볼 확률이 높다.
다음 요청은 임베딩 계산 없이 O(1) Hash 조회로 처리 가능.

### 임베딩 1회 계산 재사용

```python
embedding = await _get_embedding(normalized_query)  # 한 번만 계산
candidates = await _vector_cache.search(embedding)   # 검색에 사용
# ...
await _vector_cache.store(embedding=embedding, ...)  # 저장에 재사용
```

L2 검색과 저장 모두 같은 임베딩을 사용. 불필요한 재계산 제거.
