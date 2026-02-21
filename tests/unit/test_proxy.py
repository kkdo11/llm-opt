"""FastAPI Proxy 단위 테스트.

Redis는 mock으로 처리하여 외부 의존성 없이 테스트한다.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from src.proxy.cache.redis_cache import RedisCache, cache_key
from src.proxy.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_cache() -> MagicMock:
    """RedisCache mock 객체."""
    cache = MagicMock(spec=RedisCache)
    cache.get = AsyncMock(return_value=None)  # 기본: 캐시 미스
    cache.set = AsyncMock()
    return cache


@pytest.fixture
def client_with_mock_cache(mock_cache: MagicMock):
    """mock 캐시가 주입된 TestClient."""
    import src.proxy.main as proxy_main

    proxy_main._cache = mock_cache
    # LLM_MODE=mock 강제
    with patch.dict(os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "false"}):
        with TestClient(app) as client:
            yield client


# ---------------------------------------------------------------------------
# /health 엔드포인트 테스트
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """헬스체크 엔드포인트 테스트."""

    def test_health_returns_ok_with_redis(self, mock_cache: MagicMock) -> None:
        """Redis가 정상이면 status=ok를 반환해야 한다."""
        import src.proxy.main as proxy_main

        # AsyncMock 사용: aclose() 등 lifespan teardown에서 await가 필요하므로
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()

        with patch.dict(os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "false"}):
            with TestClient(app) as client:
                # lifespan 실행 후 mock으로 재주입 (실제 Redis 없는 테스트 환경)
                proxy_main._redis_client = mock_redis
                proxy_main._cache = mock_cache
                response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["redis"] is True
        assert data["llm_mode"] == "mock"

    def test_health_degraded_without_redis(self, mock_cache: MagicMock) -> None:
        """Redis가 없으면 status=degraded를 반환해야 한다."""
        import src.proxy.main as proxy_main

        # ping이 실패하는 mock으로 재주입 (lifespan 후 교체)
        mock_redis_fail = AsyncMock()
        mock_redis_fail.ping = AsyncMock(side_effect=Exception("connection refused"))

        with patch.dict(os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "false"}):
            with TestClient(app) as client:
                proxy_main._redis_client = mock_redis_fail
                proxy_main._cache = mock_cache
                response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["redis"] is False
        assert data["status"] == "degraded"


# ---------------------------------------------------------------------------
# /v1/chat/completions 엔드포인트 테스트
# ---------------------------------------------------------------------------


class TestChatCompletions:
    """채팅 완성 엔드포인트 테스트."""

    @pytest.mark.asyncio
    async def test_cache_miss_returns_mock_response(self, mock_cache: MagicMock) -> None:
        """캐시 미스 시 mock LLM 응답을 반환하고 cached=False여야 한다."""
        import src.proxy.main as proxy_main

        mock_cache.get = AsyncMock(return_value=None)
        proxy_main._cache = mock_cache

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.dict(os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "false"}):
                response = await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "qwen2.5:14b",
                        "messages": [{"role": "user", "content": "안녕하세요"}],
                    },
                )

        assert response.status_code == 200
        data = response.json()
        assert data["cached"] is False
        assert "content" in data
        assert data["latency_ms"] >= 0

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_response(self, mock_cache: MagicMock) -> None:
        """캐시 히트 시 저장된 응답을 반환하고 cached=True여야 한다."""
        import src.proxy.main as proxy_main

        cached_content = "이전에 캐시된 응답입니다."
        mock_cache.get = AsyncMock(return_value=cached_content)
        proxy_main._cache = mock_cache

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.dict(os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "false"}):
                response = await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "qwen2.5:14b",
                        "messages": [{"role": "user", "content": "안녕하세요"}],
                    },
                )

        assert response.status_code == 200
        data = response.json()
        assert data["cached"] is True
        assert data["content"] == cached_content

    @pytest.mark.asyncio
    async def test_cache_set_called_on_miss(self, mock_cache: MagicMock) -> None:
        """캐시 미스 후 LLM 응답을 Redis에 저장해야 한다."""
        import src.proxy.main as proxy_main

        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock()
        proxy_main._cache = mock_cache

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.dict(os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "false"}):
                await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "qwen2.5:14b",
                        "messages": [{"role": "user", "content": "새로운 질문"}],
                    },
                )

        mock_cache.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_set_not_called_on_hit(self, mock_cache: MagicMock) -> None:
        """캐시 히트 시 Redis set을 호출하지 않아야 한다."""
        import src.proxy.main as proxy_main

        mock_cache.get = AsyncMock(return_value="기존 캐시 응답")
        mock_cache.set = AsyncMock()
        proxy_main._cache = mock_cache

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.dict(os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "false"}):
                await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "qwen2.5:14b",
                        "messages": [{"role": "user", "content": "반복 질문"}],
                    },
                )

        mock_cache.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_response_has_required_fields(self, mock_cache: MagicMock) -> None:
        """응답은 id, content, cached, latency_ms 필드를 포함해야 한다."""
        import src.proxy.main as proxy_main

        mock_cache.get = AsyncMock(return_value=None)
        proxy_main._cache = mock_cache

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.dict(os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "false"}):
                response = await client.post(
                    "/v1/chat/completions",
                    json={
                        "messages": [{"role": "user", "content": "필드 확인"}],
                    },
                )

        data = response.json()
        assert "id" in data
        assert "content" in data
        assert "cached" in data
        assert "latency_ms" in data

    @pytest.mark.asyncio
    async def test_same_request_uses_same_cache_key(self, mock_cache: MagicMock) -> None:
        """동일한 요청은 동일한 캐시 키로 조회해야 한다."""
        import src.proxy.main as proxy_main

        get_calls: list[str] = []

        async def track_get(key: str):
            get_calls.append(key)
            return None

        mock_cache.get = track_get
        mock_cache.set = AsyncMock()
        proxy_main._cache = mock_cache

        messages = [{"role": "user", "content": "동일한 질문"}]
        expected_key = cache_key(messages)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.dict(os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "false"}):
                await client.post(
                    "/v1/chat/completions",
                    json={"messages": messages},
                )

        assert len(get_calls) == 1
        assert get_calls[0] == expected_key
