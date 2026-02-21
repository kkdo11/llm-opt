"""Redis Cache 단위 테스트.

외부 의존성(Redis)은 모두 mock으로 처리.
"""

import pytest

from src.proxy.cache.redis_cache import RedisCache, cache_key


# ---------------------------------------------------------------------------
# cache_key 테스트
# ---------------------------------------------------------------------------


class TestCacheKey:
    """cache_key 함수 테스트."""

    def test_same_messages_same_key(self) -> None:
        """동일한 메시지는 동일한 키를 반환해야 한다."""
        messages = [{"role": "user", "content": "안녕하세요"}]
        assert cache_key(messages) == cache_key(messages)

    def test_different_messages_different_key(self) -> None:
        """내용이 다른 메시지는 다른 키를 반환해야 한다."""
        key1 = cache_key([{"role": "user", "content": "질문 A"}])
        key2 = cache_key([{"role": "user", "content": "질문 B"}])
        assert key1 != key2

    def test_key_prefix(self) -> None:
        """키는 'llm:cache:' 접두사를 가져야 한다."""
        key = cache_key([{"role": "user", "content": "test"}])
        assert key.startswith("llm:cache:")

    def test_key_is_string(self) -> None:
        """키는 문자열이어야 한다."""
        key = cache_key([{"role": "user", "content": "test"}])
        assert isinstance(key, str)

    def test_order_insensitive_in_dict(self) -> None:
        """sort_keys=True로 직렬화하므로 dict 내 key 순서는 결과에 영향 없어야 한다."""
        msg1 = [{"content": "hello", "role": "user"}]
        msg2 = [{"role": "user", "content": "hello"}]
        assert cache_key(msg1) == cache_key(msg2)

    def test_multi_turn_messages(self) -> None:
        """멀티턴 대화도 일관된 키를 생성해야 한다."""
        messages = [
            {"role": "user", "content": "안녕"},
            {"role": "assistant", "content": "안녕하세요!"},
            {"role": "user", "content": "오늘 날씨 어때?"},
        ]
        assert cache_key(messages) == cache_key(messages)

    def test_md5_length(self) -> None:
        """MD5 해시는 32자여야 한다 (접두사 제외)."""
        key = cache_key([{"role": "user", "content": "test"}])
        md5_part = key.replace("llm:cache:", "")
        assert len(md5_part) == 32


# ---------------------------------------------------------------------------
# RedisCache 테스트 (mock Redis 사용)
# ---------------------------------------------------------------------------


class AsyncRedisMock:
    """Redis asyncio 클라이언트 Mock."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self._store.get(key)

    async def set(self, key: str, value: bytes, ex: int | None = None) -> None:
        self._store[key] = value

    def clear(self) -> None:
        self._store.clear()


@pytest.fixture
def mock_redis() -> AsyncRedisMock:
    return AsyncRedisMock()


@pytest.fixture
def redis_cache(mock_redis: AsyncRedisMock) -> RedisCache:
    return RedisCache(mock_redis, ttl=3600)  # type: ignore[arg-type]


class TestRedisCache:
    """RedisCache hit/miss 로직 테스트."""

    @pytest.mark.asyncio
    async def test_miss_returns_none(self, redis_cache: RedisCache) -> None:
        """캐시에 없는 키는 None을 반환해야 한다."""
        result = await redis_cache.get("llm:cache:nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_then_get_returns_value(self, redis_cache: RedisCache) -> None:
        """set 후 get하면 저장된 값을 반환해야 한다."""
        key = "llm:cache:abc123"
        value = "LLM 응답 내용입니다."

        await redis_cache.set(key, value)
        result = await redis_cache.get(key)

        assert result == value

    @pytest.mark.asyncio
    async def test_cache_hit_after_miss(self, redis_cache: RedisCache) -> None:
        """미스 → 저장 → 히트 시나리오를 올바르게 처리해야 한다."""
        messages = [{"role": "user", "content": "캐시 테스트"}]
        key = cache_key(messages)

        # 미스
        assert await redis_cache.get(key) is None

        # 저장
        await redis_cache.set(key, "응답")

        # 히트
        assert await redis_cache.get(key) == "응답"

    @pytest.mark.asyncio
    async def test_different_keys_independent(self, redis_cache: RedisCache) -> None:
        """다른 키는 서로 독립적으로 저장되어야 한다."""
        key1 = cache_key([{"role": "user", "content": "질문 1"}])
        key2 = cache_key([{"role": "user", "content": "질문 2"}])

        await redis_cache.set(key1, "응답 1")

        assert await redis_cache.get(key1) == "응답 1"
        assert await redis_cache.get(key2) is None

    @pytest.mark.asyncio
    async def test_overwrite_existing_value(self, redis_cache: RedisCache) -> None:
        """같은 키에 set을 두 번 호출하면 최신 값으로 덮어써야 한다."""
        key = "llm:cache:overwrite_test"
        await redis_cache.set(key, "초기 응답")
        await redis_cache.set(key, "갱신된 응답")

        assert await redis_cache.get(key) == "갱신된 응답"
