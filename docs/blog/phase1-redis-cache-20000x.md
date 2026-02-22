# LLM 프록시에 Redis 캐시를 붙였더니 응답이 20,000배 빨라졌다 (실측)

> **환경**: Python 3.12, FastAPI 0.115, Redis Stack 7.4, Qwen2.5 14B (Ollama, RTX 4080 Super)
> **측정일**: 2026-02-21

---

## 문제 상황

개인 프로젝트로 로컬 LLM 챗봇 서비스(MindGraph-AI)를 만들어서 혼자 테스트하다 이상한 점을 발견했다.
같은 질문을 여러 번 보내도 매번 LLM이 새로 추론한다. GPU는 풀가동인데 응답 내용은 거의 동일하다.

**결론부터 말하면, Redis 캐시 한 줄로 평균 응답시간이 6,065ms → 0.3ms가 됐다. 이 글은 그 과정이다.**

"이미 답한 질문을 왜 또 추론하지?"

사용 패턴을 로그로 분석해보니 문제가 명확했다.

- 테스트 중 반복적으로 유사한 질문을 보내는 패턴이 많았음
- "GPT API 비용 얼마야?", "모델 파라미터 수가 뭐야?" 같은 **반복 질문이 전체의 30~40%**
- LLM은 매번 6~11초씩 추론, GPU 메모리는 계속 점유

로컬 Ollama + Qwen2.5 14B 환경이라 API 비용은 없지만, GPU 점유 시간이 곧 비용이다.

---

## 설계

### Hash Cache가 맞는 이유

가장 단순한 구조: **완전히 동일한 요청 → MD5 해시 → Redis에서 조회**

```
요청 → MD5(messages) → Redis GET
  HIT  → 캐시 응답 즉시 반환 (LLM 미호출)
  MISS → LLM 호출 → Redis SET → 응답 반환
```

MD5를 선택한 이유:
- SHA-256보다 연산이 빠름
- 캐시 키 충돌이 목적이 아니라 **키 길이 고정(32자)**이 목적
- 암호학적 안전성 불필요

```python
import hashlib
import json

def cache_key(messages: list[dict]) -> str:
    payload = json.dumps(messages, sort_keys=True, ensure_ascii=False)
    return "llm:cache:" + hashlib.md5(payload.encode()).hexdigest()
```

`sort_keys=True`가 중요하다. JSON 직렬화 시 키 순서가 보장되지 않으면 같은 메시지가 다른 해시를 만든다.

### FastAPI Proxy 구조

```python
@app.post("/v1/chat/completions")
async def chat_completions(request: ChatRequest) -> ChatResponse:
    messages_dict = [{"role": m.role, "content": m.content} for m in request.messages]
    key = cache_key(messages_dict)

    # L1 Hash Cache 조회
    cached = await redis.get(key)
    if cached:
        return ChatResponse(content=cached, cached=True, ...)

    # LLM 호출
    content = await call_ollama(request)
    await redis.set(key, content.encode("utf-8"), ex=86400)
    return ChatResponse(content=content, cached=False, ...)
```

비동기(`async/await`)를 쓴 이유: FastAPI 이벤트 루프를 블로킹하지 않기 위해서다. `redis.get()`을 동기로 호출하면 응답을 기다리는 동안 다른 요청을 받을 수 없다.

---

## 실측 실험

### 실험 설계

| 항목 | 내용 |
|------|------|
| 고유 질문 수 | 20개 |
| 총 요청 수 | 40개 (각 질문 2회 = 중복 50%, 실제 관찰된 30~40%보다 보수적으로 높게 설정) |
| 측정 방법 | latency_ms 필드 (프록시 내부 `time.perf_counter()`) |
| LLM 백엔드 | Ollama + Qwen2.5:14b (RTX 4080 Super) |

첫 번째 요청은 LLM 호출(캐시 미스), 두 번째 요청은 Redis 조회(캐시 히트).

### 실측 결과

```
Hash Cache Hit Ratio:     50.0%   (목표 >20% ✅)
Baseline 평균 Latency:    6,065ms (캐시 미스 = LLM 실제 호출)
Cache Hit 평균 Latency:   0.3ms
속도 향상:                20,217x
API 호출 감소율:           50.0%   (목표 >20% ✅)
에러율:                   0%
```

**6,065ms → 0.3ms.** 20,000배다.

사실 이 수치는 "Redis가 빠른 게 아니라 LLM이 느린 것"이기도 하다. LLM 추론 시간을 완전히 제거했으니 당연한 결과다. 하지만 이게 핵심이다. 같은 질문에 GPU가 6초씩 낭비되고 있었다.

### LLM 응답 시간이 왜 이렇게 다를까

LLM 레이턴시 분포를 보면 흥미롭다.

```
최소: 670ms
최대: 11,059ms
평균: 6,065ms
```

같은 모델인데 670ms와 11,059ms의 차이가 왜 나는가?

**출력 토큰 수** 때문이다. "안녕하세요"에 대한 답은 짧고, "버블 정렬 구현해줘"에 대한 답은 길다. Qwen2.5 14B의 생성 속도가 일정하다면, 응답 길이에 비례해 시간이 걸린다.

캐시 레이턴시가 0.3ms인 이유도 여기 있다. Redis는 응답 길이에 무관하게 저장된 문자열을 그대로 반환하기 때문이다.

---

## 한계 발견

실험 후 명확한 한계가 드러났다.

**"파이썬 리스트 vs 튜플 차이"** 와 **"list tuple 차이가 뭐야"** 는 의미가 같지만 MD5가 다르다.

```python
cache_key([{"role":"user","content":"파이썬 리스트 vs 튜플 차이"}])
# → "llm:cache:a3f9..."

cache_key([{"role":"user","content":"list tuple 차이가 뭐야"}])
# → "llm:cache:7c2b..."  ← 완전히 다른 키
```

이 문제를 해결하려면 **의미 기반 유사도 검색**이 필요하다.

다음 글에서는 Redis Vector Search와 임베딩 모델을 붙여 이 한계를 넘는 과정을 다룬다. 단, 처음 설계한 threshold 0.85에서 히트율이 0%로 나오는 황당한 상황도 포함해서. 임베딩 모델을 잘못 고르면 아무리 threshold를 내려도 한국어 paraphrase를 잡을 수 없다.

---

## 배운 점

**1. 측정부터 해라**

"캐시를 붙이면 빨라질 것"은 누구나 안다. 하지만 **얼마나** 빨라지는지, **어떤 요청이 중복**인지는 측정해야 안다. 이 실험의 Hit Ratio 50%는 실제 관찰된 30~40% 중복률을 보수적으로 높게 설계한 수치다. 실제 운영 환경에서 중복률이 20%만 돼도 목표(>20%)는 달성된다. 핵심은 캐시를 붙이기 전에 내 서비스의 중복률이 얼마인지 먼저 로그로 측정하는 것이다. 그 수치가 캐시 설계의 출발점이다.

**2. `sort_keys=True` 빼먹으면 캐시가 전혀 안 된다**

JSON 직렬화에서 키 순서를 고정하지 않으면 같은 메시지 배열이 매번 다른 해시를 만든다. 개발 초기에 이걸 빼먹고 "왜 히트가 0%지?"를 30분 동안 디버깅했다.

**3. 비동기 Redis 클라이언트를 써야 한다**

`redis-py`의 동기 클라이언트를 FastAPI에서 쓰면 `await redis.get()` 대신 `redis.get()`을 쓰게 된다. 이 경우 Redis I/O 동안 이벤트 루프가 블로킹되어 동시 처리 성능이 급락한다. `redis.asyncio.from_url()`을 사용해야 한다.

---

## 참고

- [redis-py asyncio 공식 문서](https://redis-py.readthedocs.io/en/stable/examples/asyncio_examples.html)
- [FastAPI 공식 비동기 가이드](https://fastapi.tiangolo.com/async/)
- [Qwen2.5 모델 정보](https://huggingface.co/Qwen/Qwen2.5-14B)
