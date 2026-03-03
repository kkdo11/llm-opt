# Redis Vector Search KNN에서 발생한 두 가지 함정 — score 충돌과 잘못된 원본 매칭

## 문제 상황

LLM-OPT의 Semantic Cache를 Redis Stack HNSW 인덱스로 구현했다. 유사 쿼리가 들어오면 기존에 저장된 LLM 응답을 벡터 유사도로 찾아 반환하는 구조다.

구현 후 Threshold 실험을 돌렸는데 이상한 결과가 나왔다.

**예상:** `"파이썬 버블소트 코드 짜줘"` → KNN 상위 1개 → `"파이썬으로 버블 정렬 구현해줘"` (정답)

**실제:** `"파이썬 버블소트 코드 짜줘"` → KNN 상위 1개 → `"오버피팅이 무엇인지 설명해줘"` (엉뚱한 답)

유사도 점수를 찍어보니 더 황당했다.

```
"파이썬 버블소트 코드 짜줘" ↔ "오버피팅이 무엇인지 설명해줘" : sim=0.948
"파이썬 버블소트 코드 짜줘" ↔ "파이썬으로 버블 정렬 구현해줘" : sim=0.890
```

토픽이 완전히 다른 두 문장의 유사도가 더 높게 나왔다. 그리고 또 다른 문제도 있었다. `vec_score`가 아무리 로그를 찍어봐도 항상 `0`이 나왔다.

환경: Python 3.11, redis-py 5.x, Redis Stack 7.4

---

## 원인 분석

### 문제 1: score 속성 충돌

redis-py의 `FT.SEARCH` 결과는 `Document` 객체 리스트로 반환된다. `Document`는 `score`라는 **기본 속성**을 가지는데, 이 값은 Redis의 relevance score(기본값 0)다.

KNN 쿼리에서 거리 값을 가져오려면 `AS` 별칭을 지정해야 한다.

```python
# ❌ 잘못된 코드
Query("(*)=>[KNN 3 @embedding $vec AS score]")

# Document.score → 기본 relevance score(0), KNN 거리가 아님
raw_score = doc.score  # 항상 0
```

`AS score`로 별칭을 지정해도 `Document.score`는 기본 속성이 우선되어 KNN 거리가 덮이지 않는다.

```python
# ✅ 올바른 코드
Query("(*)=>[KNN 3 @embedding $vec AS vec_score]")

# getattr으로 접근해야 함 (속성 이름이 동적이므로)
raw_score = getattr(doc, "vec_score", 1.0)
```

### 문제 2: 잘못된 원본 매칭 (k=1의 한계)

왜 `"파이썬 버블소트"` 쿼리가 `"오버피팅 설명"`과 더 높은 유사도가 나올까?

사용 모델은 `paraphrase-multilingual-MiniLM-L12-v2`다. 이 모델은 paraphrase(패러프레이즈) 감지에 최적화되어 있어, **문장의 의미적 내용보다 문장 구조(패턴)에 민감하게 반응**하는 경향이 있다.

한국어 쿼리 패턴을 보면:

```
"파이썬으로 버블 정렬 구현해줘"   → "~을 구현해줘" 패턴
"파이썬 버블소트 코드 짜줘"       → "~코드 짜줘" 패턴
"오버피팅이 무엇인지 설명해줘"    → "~설명해줘" 패턴
```

모델이 `"짜줘"`와 `"설명해줘"`의 종결어 패턴을 유사하게 인식한다. 10개 질문을 워밍(indexing)하는 과정에서 원본끼리의 유사도 행렬을 측정해보니:

```
오버피팅 ↔ 버블정렬 : sim=0.88
스택/큐  ↔ 버블정렬 : sim=0.85
```

k=1로 검색하면 **정작 의도한 원본보다 문장 구조가 비슷한 다른 토픽의 원본이 KNN 1위**를 차지하는 상황이 발생한다.

---

## 시도한 해결책들

### 시도 1: threshold 올리기

k=1에서 threshold를 0.85 → 0.90으로 올렸다.

**결과:** 히트 자체가 0%가 됐다. 의도한 paraphrase 쌍의 유사도도 0.87~0.94 범위라 threshold 위아래로 뭉쳐 있어서 올리면 다 막혀버렸다.

### 시도 2: k=3으로 상위 후보 확장 + Validation Layer 순차 적용

k=3으로 상위 3개 후보를 가져온 뒤, 각 후보에 Validation을 순차 적용해 통과하는 첫 번째 후보를 선택한다.

Validation Layer는 언어 일치, 숫자/연도 불일치, 기술 키워드 불일치를 검사한다. 엉뚱한 토픽(`"오버피팅 설명"`)의 키워드와 `"파이썬 버블소트"`의 키워드는 다르기 때문에 필터링할 수 있다.

**결과:** 잘못된 원본 매칭 문제 해결. 아래에 상세 설명.

---

## 최종 해결

### score 충돌 수정

```python
# vector_cache.py

# 주의: KNN 별칭을 'score'로 지정하면 redis-py Document의 기본 score(0) 속성과 충돌한다.
# 'vec_score'로 별칭을 분리해 KNN 거리를 올바르게 파싱한다.
query = (
    Query("(*)=>[KNN 3 @embedding $vec AS vec_score]")
    .sort_by("vec_score")
    .return_fields("vec_score", "$.content", "$.model", "$.lang", "$.keywords")
    .dialect(2)
)

for doc in results.docs:
    raw_score = getattr(doc, "vec_score", 1.0)
    if isinstance(raw_score, bytes):
        raw_score = raw_score.decode("utf-8")
    similarity = 1.0 - float(raw_score)  # COSINE 거리 → 유사도 변환
```

### k=3 + Validation 순차 적용

```python
# main.py (흐름 요약)

# L2: 상위 3개 후보 검색
candidates = await _vector_cache.search(embedding)  # [(content, sim, metadata), ...]

# 각 후보에 Validation 적용, 첫 통과자 선택
for content, similarity, metadata in candidates:
    result = validator.validate(
        query_text=query_text,
        query_lang=query_lang,
        cached_metadata=metadata,
        similarity=similarity,
    )
    if result.passed:
        return cached_response  # L2 히트
```

```python
# vector_cache.py — KNN k=3
# KNN 3: k=1은 문장 구조 유사 원본에 잘못 매칭될 수 있으므로 3개 후보를 검색한다.
query = Query("(*)=>[KNN 3 @embedding $vec AS vec_score]")
```

### 검증 결과 (실측, 2026-02-22)

```
질문 쌍: 10개 (expected_hit=True 6개, expected_hit=False 4개)
threshold: 0.75

보완 전 → 보완 후
Hit Ratio:   50.0% → 66.7%   (+16.7%)
실질 FP:      0.0% →  0.0%   (유지)
FN Rate:     50.0% → 33.3%   (-16.7% 개선)
```

---

## COSINE 거리 vs 유사도 변환

Redis Stack HNSW는 COSINE **거리**를 반환한다 (0=동일, 2=완전 반대). 이를 유사도로 변환하는 공식은:

```python
similarity = 1.0 - distance  # 유사도 범위: [-1, 1], 보통 [0, 1]
```

threshold와 비교할 때 반드시 변환 후 비교해야 한다. 변환 없이 raw distance를 threshold와 비교하면 논리가 반대가 된다.

```python
# ❌ 잘못된 비교
if raw_score > threshold:  # distance가 크면 더 다른 것인데 히트로 처리됨

# ✅ 올바른 비교
similarity = 1.0 - float(raw_score)
if similarity >= threshold:  # 유사도가 높으면 히트
```

---

## 배운 점

**1. redis-py `Document.score`는 KNN 거리가 아니다**

`AS score`로 별칭을 지정해도 기본 `score` 속성이 우선된다. KNN 거리는 반드시 `score`가 아닌 다른 이름(`vec_score` 등)으로 별칭을 지정하고 `getattr`로 접근해야 한다.

**2. k=1 KNN은 paraphrase 특화 모델과 궁합이 나쁠 수 있다**

문장 구조 패턴에 민감한 모델을 쓸 때 k=1이면 의도치 않은 원본이 1위를 차지할 수 있다. k를 늘리고 후처리 필터를 거는 방식이 더 안전하다.

**3. COSINE 거리와 유사도를 혼동하지 말자**

Redis Stack은 거리를 반환하고, 우리가 threshold로 비교하는 건 유사도다. `1.0 - distance` 변환을 빠트리면 로직이 조용히 반대로 동작한다.

**4. 임베딩 모델의 한계를 실험으로 확인하자**

"오버피팅 vs 버블정렬 sim=0.88" 같은 수치는 모델을 써보기 전엔 알 수 없다. 실제 데이터로 원본 간 유사도 행렬을 미리 측정해보면 threshold와 k 설정의 기준이 생긴다.

---

**참고**

- [Redis Vector Search — KNN Query Syntax](https://redis.io/docs/latest/develop/interact/search-and-query/search/vectors/)
- [redis-py — Search 결과 Document 구조](https://redis-py.readthedocs.io/en/stable/commands.html#search-commands)
