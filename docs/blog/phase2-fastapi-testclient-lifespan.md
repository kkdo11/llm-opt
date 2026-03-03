# FastAPI TestClient + lifespan에서 mock이 덮어씌워지는 문제

## 문제 상황

FastAPI 앱에 lifespan을 붙여 서버 시작 시 Redis 연결을 초기화하고 있었다.

```python
# main.py

from contextlib import asynccontextmanager

_redis_client: Redis | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _redis_client
    _redis_client = await Redis.from_url(os.environ["REDIS_URL"])  # 실제 Redis 연결
    yield
    await _redis_client.aclose()

app = FastAPI(lifespan=lifespan)
```

테스트에서 `_redis_client`를 mock으로 바꿔치기하려고 이렇게 짰다.

```python
# test_proxy.py (잘못된 코드)

def test_health_degraded_without_redis():
    import src.proxy.main as proxy_main

    # ❌ TestClient 생성 전에 mock 주입
    proxy_main._redis_client = MagicMock()
    proxy_main._redis_client.ping = AsyncMock(side_effect=ConnectionError)

    with TestClient(app) as client:
        response = client.get("/health")
        assert response.json()["redis"] is False  # 실패!
```

테스트를 실행하면 `redis` 값이 `False`가 아니라 `True`가 나왔다. mock이 동작하지 않는 것처럼 보였다.

환경: Python 3.11, FastAPI 0.115, pytest 8.x

---

## 원인 분석

`TestClient(app)`은 내부적으로 `anyio`를 사용해 앱의 lifespan을 실행한다.

핵심은 **`with TestClient(app) as client:` 블록에 진입하는 순간 lifespan이 실행된다**는 점이다.

```
1. proxy_main._redis_client = MagicMock()  ← mock 주입
2. with TestClient(app) as client:          ← 여기서 lifespan 실행
   └─ lifespan: _redis_client = Redis.from_url(...)  ← 실제 Redis로 덮어씌워짐
3. client.get("/health")                   ← 이미 mock이 아님
```

즉 `with` 블록 **진입 전**에 주입한 mock이 lifespan에 의해 덮어씌워진다.

이를 확인하기 위해 print를 찍어봤다.

```python
def test_debug():
    import src.proxy.main as proxy_main

    proxy_main._redis_client = MagicMock()
    print("before:", id(proxy_main._redis_client))  # MagicMock의 id

    with TestClient(app) as client:
        print("inside:", id(proxy_main._redis_client))  # 다른 id (실제 Redis)
```

출력:
```
before: 140234567890   ← MagicMock
inside: 140234999999   ← 실제 Redis 객체
```

lifespan이 `with` 블록 진입 시 실행되면서 모듈 전역변수를 새 객체로 교체했다.

---

## 시도한 해결책들

### 시도 1: with 블록 밖에서 mock 주입 → 실패

위에서 설명한 방법. lifespan이 덮어쓴다.

### 시도 2: lifespan을 patch로 무력화 → 번거롭고 부작용

```python
with patch("src.proxy.main.lifespan", AsyncMock()):
    with TestClient(app) as client:
        ...
```

lifespan을 통째로 막으면 `_redis_client`가 아예 초기화되지 않아, 앱의 다른 부분에서 `None` 참조 오류가 날 수 있다. 또 lifespan 로직 자체를 테스트하지 못하게 된다.

### 시도 3: with 블록 내부에서 mock 재주입 → 성공

lifespan이 완료된 **후**에 mock을 주입하면 된다.

```python
with TestClient(app) as client:
    # lifespan 완료 후 재주입
    proxy_main._redis_client = mock_redis
    response = client.get("/health")
```

---

## 최종 해결

```python
# test_proxy.py

def test_health_degraded_without_redis(mock_cache: MagicMock) -> None:
    """Redis가 없으면 status=degraded를 반환해야 한다."""
    import src.proxy.main as proxy_main

    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock(side_effect=ConnectionError("Redis 연결 실패"))

    with patch.dict(os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "false"}):
        with TestClient(app) as client:
            # ✅ lifespan 완료 후 mock 재주입
            proxy_main._redis_client = mock_redis
            proxy_main._cache = mock_cache

            response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["redis"] is False
    assert response.json()["status"] == "degraded"
```

`SEMANTIC_CACHE_ENABLED=false`도 중요하다. `true`면 lifespan에서 `SentenceTransformer`를 로드하려 하는데, 테스트 환경에서 모델 파일이 없거나 로딩이 느리다. 환경변수로 끄면 lifespan이 가볍게 완료된다.

### 부수 문제: event loop mismatch

Phase 3에서 `QuotaTracker`를 추가한 후 다른 테스트에서 이런 오류가 났다.

```
RuntimeError: Task <Task ...> attached to a different loop
```

원인은 이전 테스트의 lifespan에서 생성된 `_quota_tracker`(내부에 Redis 클라이언트 보유)가 다음 테스트의 새 event loop에서 참조되는 것이었다.

fixture에서 명시적으로 초기화해 해결했다.

```python
@pytest.fixture
def mock_cache() -> MagicMock:
    import src.proxy.main as proxy_main
    proxy_main._quota_tracker = None  # 이전 lifespan 잔류 상태 격리
    cache = MagicMock(spec=RedisCache)
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock()
    return cache
```

---

## 배운 점

**1. `with TestClient(app) as client:` 진입 시 lifespan이 실행된다**

이 타이밍을 인지하지 못하면 mock 주입이 조용히 무시되는 버그를 만든다. 항상 `with` 블록 **내부**에서 mock을 재주입하거나, lifespan이 끝난 후 상태를 덮어써야 한다.

**2. lifespan에서 초기화하는 상태는 테스트 간 격리가 필요하다**

모듈 전역변수(`_redis_client`, `_quota_tracker` 등)는 테스트 간에 공유된다. fixture에서 명시적으로 초기화하지 않으면 이전 테스트의 상태가 남아 예상치 못한 오류를 만든다.

**3. SEMANTIC_CACHE_ENABLED=false로 무거운 lifespan을 막자**

SentenceTransformer 로딩은 수 초가 걸린다. 테스트에서 필요하지 않다면 환경변수로 비활성화해 테스트 실행 시간을 줄이자.

---

**참고**

- [FastAPI docs — Lifespan Events](https://fastapi.tiangolo.com/advanced/events/)
- [Starlette TestClient 소스](https://github.com/encode/starlette/blob/master/starlette/testclient.py)
