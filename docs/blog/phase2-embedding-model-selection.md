# 한국어 Semantic Cache에서 임베딩 모델 선택이 왜 중요한가 — all-MiniLM vs multilingual 실측

> **환경**: Python 3.12, sentence-transformers 3.x, Redis Stack 7.4 (HNSW)
> **측정일**: 2026-02-21

---

## 문제 상황

Semantic Cache를 구현했다. 의미가 유사한 질문은 Vector Search로 캐시를 히트시키는 구조다.

```
"파이썬으로 버블 정렬 구현해줘" → 캐시 저장
"파이썬 버블소트 코드 짜줘"     → 캐시 히트 ← 이게 되어야 한다
```

처음 선택한 임베딩 모델은 `all-MiniLM-L6-v2`다. HuggingFace에서 가장 많이 쓰이는 경량 모델. 384차원, 빠른 추론 속도.

테스트 결과:

```python
from sentence_transformers import SentenceTransformer, util

model = SentenceTransformer("all-MiniLM-L6-v2")

a = model.encode("파이썬으로 버블 정렬 구현해줘", normalize_embeddings=True)
b = model.encode("파이썬 버블소트 코드 짜줘", normalize_embeddings=True)

print(util.cos_sim(a, b).item())
# 0.63
```

**0.63.** threshold가 0.85이니 완전 캐시 미스다.

**결론부터 말하면: 모델 교체 후 한국어 paraphrase 유사도 0.63 → 0.91, Hit Ratio 66.7% 달성. 이 글은 어떤 모델로 교체했는지, 그리고 교체 후 새로 생긴 문제를 어떻게 막았는지에 대한 기록이다.**

---

## 원인 분석

### all-MiniLM-L6-v2는 영어 최적화 모델이다

공식 설명을 보면:

> "This model is intended to be used as a sentence and short paragraph encoder. Given an input text, it outputs a vector which captures the semantic information."

학습 데이터가 **영어 위주**다. 한국어 paraphrase를 처리하면 어떻게 되는지 직접 측정했다.

```
[영어 paraphrase 쌍]
"How to sort a list in Python" ↔ "Python list sorting methods"
cos_sim = 0.89 ← threshold 통과

[한국어 paraphrase 쌍]
"파이썬으로 버블 정렬 구현해줘" ↔ "파이썬 버블소트 코드 짜줘"
cos_sim = 0.63 ← threshold 미달
```

한국어 paraphrase는 의미가 같아도 유사도가 0.63으로 낮게 나온다. 모델이 한국어 표현의 의미적 동일성을 포착하지 못한다.

### 유사도 분포가 문제다

**all-MiniLM-L6-v2** 기준으로 threshold=0.85를 적용했을 때, 한국어 질문 쌍의 유사도 분포:

```
실제로 다른 질문들: 0.70 ~ 0.90  ← 너무 높음 (False Positive 위험)
실제로 같은 질문들: 0.55 ~ 0.87  ← 너무 낮음 (False Negative 발생)
```

다른 질문과 같은 질문의 유사도 범위가 겹친다. threshold로 구분이 불가능한 상황이다.

---

## 모델 교체 실험

### 후보 선정

한국어 텍스트를 처리할 수 있는 sentence-transformers 계열 모델을 조사했다.

| 모델 | 차원 | 특징 |
|------|------|------|
| all-MiniLM-L6-v2 | 384 | 영어 최적화, 가장 빠름 |
| paraphrase-multilingual-MiniLM-L12-v2 | 384 | 50개 언어, paraphrase 학습 |
| ko-sroberta-multitask | 768 | 한국어 특화, STS 성능 높음 |

`ko-sroberta-multitask`는 768차원이라 HNSW 인덱스를 재생성해야 한다. Phase 1에서 384차원으로 설계한 인덱스와 호환되지 않는다. 인덱스 재생성 = 기존 캐시 전체 무효화. 운영 중인 서비스라면 수십만 건의 임베딩을 다시 계산해야 하는 셈이다. 개인 프로젝트라 규모가 작았지만, 설계 원칙으로 "불필요한 인덱스 재생성은 피한다"를 지켰다.

`paraphrase-multilingual-MiniLM-L12-v2`는 384차원 그대로라 인덱스 유지 가능. 이름에 "paraphrase"가 들어간 것도 paraphrase 감지가 목적인 Semantic Cache에 적합하다.

### 실측 비교

같은 한국어 질문 쌍으로 두 모델을 비교:

```
[all-MiniLM-L6-v2]
"파이썬 버블 정렬 구현해줘" ↔ "파이썬 버블소트 코드 짜줘":  0.63
"리스트와 튜플의 차이점"    ↔ "list tuple 차이가 뭐야":    0.32

[paraphrase-multilingual-MiniLM-L12-v2]
"파이썬 버블 정렬 구현해줘" ↔ "파이썬 버블소트 코드 짜줘":  0.91 ✅
"리스트와 튜플의 차이점"    ↔ "list tuple 차이가 뭐야":    0.77 (정규화 전)
                                                            0.94 ✅ (정규화 후)
```

한국어 paraphrase 유사도가 0.32~0.77에서 0.87~0.95로 올랐다.

---

## 교체 후 나타난 새로운 문제

### False Positive 증가 위험

모델을 바꾸니 유사도가 전반적으로 올랐다. 그런데 **달라야 하는 질문들도 유사도가 올랐다**.

```
"TCP와 UDP의 차이점" ↔ "HTTP와 HTTPS의 차이점": sim = 0.78
```

모두 "~의 차이점"이라는 구조를 공유하고, 모두 네트워크 관련 질문이다. threshold=0.75에서 히트된다.

하지만 TCP/UDP 답변과 HTTP/HTTPS 답변은 내용이 완전히 다르다. **이게 False Positive다.**

### Validation Layer로 해결

유사도 threshold만으로 판단하는 게 위험하다. 추가 검증이 필요하다.

3단계 검증을 구현했다:

```python
class SemanticValidator:
    def validate(self, query_text, query_lang, cached_metadata, similarity):
        # 1단계: 언어 일치
        if query_lang and cached_lang and query_lang != cached_lang:
            return ValidationResult(passed=False, reason="언어 불일치")

        # 2단계: 숫자/연도/버전 키워드
        query_nums = extract_numeric_keywords(query_text)
        cached_nums = cached_metadata.get("keywords", [])
        if query_nums and not query_nums.issubset(cached_nums):
            return ValidationResult(passed=False, reason="숫자 키워드 불일치")

        # 3단계: 기술 키워드 (프로그래밍 언어, 프로토콜)
        if not tech_keywords_compatible(query_text, cached_metadata):
            return ValidationResult(passed=False, reason="기술 키워드 불일치")

        return ValidationResult(passed=True)
```

3단계의 핵심은 프로토콜 키워드 집합 비교다. TCP와 HTTP가 같은 네트워크 계층이 아니라는 도메인 지식을 코드로 표현한 것이다.

```python
NETWORK_PROTOCOLS = frozenset({
    "tcp", "udp", "http", "https",
    "grpc", "websocket", "ws", "mqtt",
    "ftp", "smtp", "dns", "ssl", "tls",
})

def validate_tech_keywords(query_text: str, cached_keywords: list[str]) -> bool:
    query_lower = query_text.lower()
    cached_text = " ".join(k.lower() for k in cached_keywords)

    # 쿼리에서 프로토콜 키워드 추출 (PROGRAMMING_LANGUAGES도 동일 방식으로 검사)
    query_protocols = {p for p in NETWORK_PROTOCOLS if p in query_lower}
    cached_protocols = {p for p in NETWORK_PROTOCOLS if p in cached_text}

    if query_protocols and query_protocols != cached_protocols:
        return False  # "tcp" 질문에 "http" 캐시 반환 차단
    return True
```

실제 코드는 프로토콜과 프로그래밍 언어 키워드를 모두 `"keywords"` 필드 하나에 저장한다. `cached_metadata["keywords"]`에 `["tcp", "udp"]` 같은 값이 들어있고, `validate_tech_keywords()`가 이를 꺼내 비교한다.

TCP/UDP vs HTTP/HTTPS는 이 검증에서 걸린다. `query_protocols={"tcp"}`, `cached_protocols={"http"}` → 불일치 → 캐시 미스 처리.

---

## 최종 실측 결과

```
모델: paraphrase-multilingual-MiniLM-L12-v2
Threshold: 0.75
Validation Layer: 3단계 (언어 / 숫자 / 기술 키워드)

Hit Ratio:      66.7%   (목표 >40% ✅)
False Positive: 0%      (목표 <5% ✅)
```

Semantic Cache가 Hash Cache보다 더 많은 질문을 잡아낸 이유: "파이썬 버블 정렬 구현해줘"와 "파이썬 버블소트 코드 짜줘"처럼 Hash가 다르지만 의미가 같은 질문을 임베딩 유사도로 히트시켰기 때문이다.

> 단, Phase 1(40개 요청, 실 Ollama 환경)과 Phase 2(10개 질문 쌍, mock 환경)는 실험 규모와 조건이 다르다. 수치를 직접 비교하기보다 "각 방식이 어떤 유형의 중복을 잡는가"의 차이로 해석하는 것이 맞다.

---

## 배운 점

**1. 임베딩 모델은 학습 언어에 강하게 의존한다**

"가장 많이 쓰이는 모델"이 우리 언어/도메인에 맞는 모델이 아닐 수 있다. 한국어 서비스라면 한국어 텍스트에서 직접 유사도를 측정해보고 결정해야 한다.

**2. 모델 교체 비용을 사전에 설계에 반영하라**

차원이 같으면 인덱스를 유지할 수 있다. 384→768차원 변경은 인덱스 재생성 + 기존 캐시 무효화를 의미한다. 처음부터 "모델 교체 가능성"을 염두에 두고 차원과 인덱스 구조를 설계하면 나중에 비용이 줄어든다.

**3. threshold만으로 의미 구분은 불충분하다**

Vector Search는 "얼마나 비슷한가"를 수치로 준다. 하지만 "어떤 면에서 비슷한가"는 알려주지 않는다. "TCP/UDP 차이"와 "HTTP/HTTPS 차이"는 구조적으로 비슷하지만 내용은 다르다. 도메인 지식 기반의 추가 검증이 필요하다.

**4. 오탐 방지와 유사도 향상은 같은 문제의 두 면이다**

langdetect 오인식을 막으려고 만든 쿼리 정규화가 임베딩 유사도도 올렸다. 영어 기술 용어의 혼재가 두 문제의 공통 원인이었다. 근본 원인을 찾으면 하나의 해결책으로 여러 증상을 고칠 수 있다.

---

## 관련 포스트

- [Phase 1: LLM 프록시에 Redis 캐시를 붙였더니 20,000배 빨라졌다](./phase1-redis-cache-20000x.md)
- ["파이썬 list와 tuple"을 에스토니아어로 인식한 langdetect](./phase2-semantic-cache-langdetect.md)
