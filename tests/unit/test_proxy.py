"""FastAPI Proxy 단위 테스트.

Redis는 mock으로 처리하여 외부 의존성 없이 테스트한다.
"""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from src.proxy.cache.redis_cache import RedisCache, cache_key
from src.proxy.main import app
from src.proxy.rate_limit.quota_tracker import QuotaStatus
from src.proxy.validation.validator import ValidationResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_cache() -> MagicMock:
    """RedisCache mock 객체.

    Phase 3: _quota_tracker도 None으로 초기화하여 이전 lifespan의
    Redis 클라이언트가 잔류하는 event loop mismatch를 방지한다.
    """
    import src.proxy.main as proxy_main
    proxy_main._quota_tracker = None  # lifespan 잔류 상태 격리
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


# ---------------------------------------------------------------------------
# L1→L2→LLM 파이프라인 통합 테스트
# ---------------------------------------------------------------------------


class TestL2SemanticCachePipeline:
    """L2 Semantic Cache 경로 통합 테스트."""

    @pytest.mark.asyncio
    async def test_l2_semantic_hit_returns_cached_response(self) -> None:
        """L1 미스 → L2 히트 → validation 통과 → L1 백필 → cached=True 응답."""
        import src.proxy.main as proxy_main

        mock_cache = MagicMock(spec=RedisCache)
        mock_cache.get = AsyncMock(return_value=None)   # L1 미스
        mock_cache.set = AsyncMock()

        mock_vector_cache = MagicMock()
        mock_vector_cache.search = AsyncMock(return_value=[
            ("L2에서 찾은 캐시 응답", 0.92, {"lang": "ko", "keywords": ["파이썬"]}),
        ])

        mock_validator = MagicMock()
        mock_validator.validate = MagicMock(
            return_value=ValidationResult(passed=True, reason="통과")
        )

        proxy_main._cache = mock_cache
        proxy_main._quota_tracker = None
        proxy_main._vector_cache = mock_vector_cache
        proxy_main._validator = mock_validator
        proxy_main._embedding_model = MagicMock()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.dict(os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "true"}):
                with patch("src.proxy.main._get_embedding", return_value=np.zeros(384)):
                    with patch("src.proxy.main._detect_lang", return_value="ko"):
                        response = await client.post(
                            "/v1/chat/completions",
                            json={"messages": [{"role": "user", "content": "파이썬 리스트 정렬 방법"}]},
                        )

        assert response.status_code == 200
        data = response.json()
        assert data["cached"] is True
        assert data["tier"] == "l2_semantic"
        assert data["content"] == "L2에서 찾은 캐시 응답"
        # L1 백필 확인
        mock_cache.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_l2_hit_validation_fails_falls_through_to_llm(self) -> None:
        """L1 미스 → L2 후보 있지만 validation 실패 → LLM mock 호출 → cached=False."""
        import src.proxy.main as proxy_main

        mock_cache = MagicMock(spec=RedisCache)
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock()

        mock_vector_cache = MagicMock()
        mock_vector_cache.search = AsyncMock(return_value=[
            ("자바 리스트 정렬", 0.88, {"lang": "ko", "keywords": ["자바"]}),
        ])
        mock_vector_cache.store = AsyncMock()

        mock_validator = MagicMock()
        mock_validator.validate = MagicMock(
            return_value=ValidationResult(passed=False, reason="기술 키워드 불일치: python vs java")
        )

        proxy_main._cache = mock_cache
        proxy_main._quota_tracker = None
        proxy_main._vector_cache = mock_vector_cache
        proxy_main._validator = mock_validator
        proxy_main._embedding_model = MagicMock()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.dict(os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "true"}):
                with patch("src.proxy.main._get_embedding", return_value=np.zeros(384)):
                    with patch("src.proxy.main._detect_lang", return_value="ko"):
                        response = await client.post(
                            "/v1/chat/completions",
                            json={"messages": [{"role": "user", "content": "파이썬 리스트 정렬"}]},
                        )

        assert response.status_code == 200
        data = response.json()
        assert data["cached"] is False
        assert "MOCK" in data["content"]

    @pytest.mark.asyncio
    async def test_l2_miss_stores_to_both_caches(self) -> None:
        """L1 미스 → L2 미스 → LLM 호출 → L1+L2 모두 저장."""
        import src.proxy.main as proxy_main

        mock_cache = MagicMock(spec=RedisCache)
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock()

        mock_vector_cache = MagicMock()
        mock_vector_cache.search = AsyncMock(return_value=[])  # L2 미스
        mock_vector_cache.store = AsyncMock()

        proxy_main._cache = mock_cache
        proxy_main._quota_tracker = None
        proxy_main._vector_cache = mock_vector_cache
        proxy_main._validator = MagicMock()
        proxy_main._embedding_model = MagicMock()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.dict(os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "true"}):
                with patch("src.proxy.main._get_embedding", return_value=np.zeros(384)):
                    with patch("src.proxy.main._detect_lang", return_value="ko"):
                        response = await client.post(
                            "/v1/chat/completions",
                            json={"messages": [{"role": "user", "content": "도커 컨테이너란"}]},
                        )

        assert response.status_code == 200
        data = response.json()
        assert data["cached"] is False
        # L1 저장 확인
        mock_cache.set.assert_called_once()
        # L2 저장 확인
        mock_vector_cache.store.assert_called_once()

    @pytest.mark.asyncio
    async def test_quota_exceeded_returns_429(self) -> None:
        """할당량 초과 시 HTTP 429를 반환해야 한다."""
        import src.proxy.main as proxy_main

        mock_cache = MagicMock(spec=RedisCache)
        mock_cache.get = AsyncMock(return_value=None)  # L1 미스 → LLM 경로 진입

        mock_quota = MagicMock()
        mock_quota.check = AsyncMock(return_value=QuotaStatus.EXCEEDED)

        proxy_main._cache = mock_cache
        proxy_main._quota_tracker = mock_quota
        proxy_main._vector_cache = None  # L2 비활성

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.dict(os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "false"}):
                response = await client.post(
                    "/v1/chat/completions",
                    json={"messages": [{"role": "user", "content": "할당량 초과 테스트"}]},
                )

        assert response.status_code == 429

    @pytest.mark.asyncio
    async def test_stream_l1_hit_returns_sse(self) -> None:
        """L1 히트 + stream=True → text/event-stream 형식으로 반환해야 한다."""
        import src.proxy.main as proxy_main

        mock_cache = MagicMock(spec=RedisCache)
        mock_cache.get = AsyncMock(return_value="스트리밍 캐시 응답")

        proxy_main._cache = mock_cache
        proxy_main._quota_tracker = None
        proxy_main._vector_cache = None

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.dict(os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "false"}):
                response = await client.post(
                    "/v1/chat/completions",
                    json={
                        "messages": [{"role": "user", "content": "스트리밍 테스트"}],
                        "stream": True,
                    },
                )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        body = response.text
        assert "done" in body
        assert "true" in body

    @pytest.mark.asyncio
    async def test_long_query_skips_l2(self) -> None:
        """300자 초과 쿼리는 L2 Semantic Cache를 건너뛰어야 한다 (RAG 오염 방지)."""
        import src.proxy.main as proxy_main

        mock_cache = MagicMock(spec=RedisCache)
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock()

        mock_vector_cache = MagicMock()
        mock_vector_cache.search = AsyncMock(return_value=[])

        proxy_main._cache = mock_cache
        proxy_main._quota_tracker = None
        proxy_main._vector_cache = mock_vector_cache
        proxy_main._embedding_model = MagicMock()

        long_query = "이것은 300자를 넘는 긴 RAG 프롬프트입니다. " * 15  # 약 450자

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.dict(os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "true"}):
                response = await client.post(
                    "/v1/chat/completions",
                    json={"messages": [{"role": "user", "content": long_query}]},
                )

        assert response.status_code == 200
        # L2 search가 호출되지 않아야 함
        mock_vector_cache.search.assert_not_called()


# ---------------------------------------------------------------------------
# 추가 커버리지 테스트
# ---------------------------------------------------------------------------


class TestEdgeCasesAndCoverage:
    """커버리지 누락 경로 보완 테스트."""

    @pytest.mark.asyncio
    async def test_cache_not_initialized_returns_503(self) -> None:
        """_cache가 None이면 503을 반환해야 한다 (line 243)."""
        import src.proxy.main as proxy_main

        proxy_main._cache = None

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "테스트"}]},
            )

        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_l2_stream_hit_returns_sse(self) -> None:
        """L1 미스 → L2 히트 → stream=True → SSE 반환 (line 321)."""
        import src.proxy.main as proxy_main

        mock_cache = MagicMock(spec=RedisCache)
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock()

        mock_vector_cache = MagicMock()
        mock_vector_cache.search = AsyncMock(return_value=[
            ("캐시된 스트리밍 응답", 0.92, {"lang": "ko", "keywords": ["파이썬"]}),
        ])

        mock_validator = MagicMock()
        mock_validator.validate = MagicMock(
            return_value=ValidationResult(passed=True, reason="통과")
        )

        proxy_main._cache = mock_cache
        proxy_main._quota_tracker = None
        proxy_main._vector_cache = mock_vector_cache
        proxy_main._validator = mock_validator
        proxy_main._embedding_model = MagicMock()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.dict(os.environ, {"LLM_MODE": "mock"}):
                with patch("src.proxy.main._get_embedding", return_value=np.zeros(384)):
                    with patch("src.proxy.main._detect_lang", return_value="ko"):
                        response = await client.post(
                            "/v1/chat/completions",
                            json={
                                "messages": [{"role": "user", "content": "파이썬 리스트 정렬"}],
                                "stream": True,
                            },
                        )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        assert "캐시된 스트리밍 응답" in response.text

    @pytest.mark.asyncio
    async def test_quota_warning_continues_to_llm(self) -> None:
        """할당량 80%~99%(WARNING)이면 LLM을 계속 호출해야 한다 (lines 344-345)."""
        import src.proxy.main as proxy_main

        mock_cache = MagicMock(spec=RedisCache)
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock()

        mock_quota = MagicMock()
        mock_quota.check = AsyncMock(return_value=QuotaStatus.WARNING)
        mock_quota.increment = AsyncMock(return_value=85000)

        proxy_main._cache = mock_cache
        proxy_main._quota_tracker = mock_quota
        proxy_main._vector_cache = None

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.dict(os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "false"}):
                response = await client.post(
                    "/v1/chat/completions",
                    json={"messages": [{"role": "user", "content": "할당량 경고 테스트"}]},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["cached"] is False
        mock_quota.increment.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_llm_increments_quota_and_stores_l2(self) -> None:
        """stream=True + LLM miss → quota increment + L2 store 모두 호출 (lines 371, 375-377)."""
        import src.proxy.main as proxy_main

        mock_cache = MagicMock(spec=RedisCache)
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock()

        mock_vector_cache = MagicMock()
        mock_vector_cache.search = AsyncMock(return_value=[])
        mock_vector_cache.store = AsyncMock()

        mock_quota = MagicMock()
        mock_quota.check = AsyncMock(return_value=QuotaStatus.OK)
        mock_quota.increment = AsyncMock(return_value=100)

        proxy_main._cache = mock_cache
        proxy_main._quota_tracker = mock_quota
        proxy_main._vector_cache = mock_vector_cache
        proxy_main._validator = MagicMock()
        proxy_main._embedding_model = MagicMock()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.dict(os.environ, {"LLM_MODE": "mock"}):
                with patch("src.proxy.main._get_embedding", return_value=np.zeros(384)):
                    with patch("src.proxy.main._detect_lang", return_value="ko"):
                        response = await client.post(
                            "/v1/chat/completions",
                            json={
                                "messages": [{"role": "user", "content": "스트리밍 할당량 테스트"}],
                                "stream": True,
                            },
                        )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        mock_quota.increment.assert_called_once()
        mock_vector_cache.store.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_stream_llm_increments_quota(self) -> None:
        """stream=False + LLM miss → quota increment 호출 (line 429)."""
        import src.proxy.main as proxy_main

        mock_cache = MagicMock(spec=RedisCache)
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock()

        mock_quota = MagicMock()
        mock_quota.check = AsyncMock(return_value=QuotaStatus.OK)
        mock_quota.increment = AsyncMock(return_value=100)

        proxy_main._cache = mock_cache
        proxy_main._quota_tracker = mock_quota
        proxy_main._vector_cache = None

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.dict(os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "false"}):
                response = await client.post(
                    "/v1/chat/completions",
                    json={"messages": [{"role": "user", "content": "할당량 증가 테스트"}]},
                )

        assert response.status_code == 200
        mock_quota.increment.assert_called_once()

    def test_health_with_llm_backend(self, mock_cache: MagicMock) -> None:
        """_llm_backend가 설정된 경우 health_check()를 호출해야 한다 (line 486)."""
        import src.proxy.main as proxy_main

        mock_backend = MagicMock()
        mock_backend.health_check = AsyncMock(return_value=True)

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()

        original_backend = proxy_main._llm_backend
        try:
            with patch.dict(os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "false"}):
                with TestClient(app) as client:
                    proxy_main._redis_client = mock_redis
                    proxy_main._cache = mock_cache
                    proxy_main._llm_backend = mock_backend
                    response = client.get("/health")
        finally:
            proxy_main._llm_backend = original_backend

        assert response.status_code == 200
        data = response.json()
        assert data["llm_backend"]["connected"] is True

    @pytest.mark.asyncio
    async def test_detect_lang_returns_empty_on_error(self) -> None:
        """langdetect 실패 시 빈 문자열을 반환해야 한다 (lines 206-210)."""
        from src.proxy.main import _detect_lang

        with patch("langdetect.detect", side_effect=Exception("detection failed")):
            result = await _detect_lang("some text")

        assert result == ""

    @pytest.mark.asyncio
    async def test_get_embedding_raises_when_model_none(self) -> None:
        """_embedding_model이 None이면 RuntimeError를 발생시켜야 한다 (lines 193-194)."""
        import src.proxy.main as proxy_main
        from src.proxy.main import _get_embedding

        original = proxy_main._embedding_model
        proxy_main._embedding_model = None
        try:
            with pytest.raises(RuntimeError, match="임베딩 모델이 초기화되지 않았습니다"):
                await _get_embedding("테스트")
        finally:
            proxy_main._embedding_model = original

    @pytest.mark.asyncio
    async def test_get_embedding_returns_vector_when_model_set(self) -> None:
        """_embedding_model이 설정된 경우 임베딩 벡터를 반환해야 한다 (line 195)."""
        import src.proxy.main as proxy_main
        from src.proxy.main import _get_embedding

        expected = np.zeros(384, dtype=np.float32)
        mock_model = MagicMock()
        mock_model.encode = MagicMock(return_value=expected)

        original = proxy_main._embedding_model
        proxy_main._embedding_model = mock_model
        try:
            result = await _get_embedding("테스트 텍스트")
        finally:
            proxy_main._embedding_model = original

        np.testing.assert_array_equal(result, expected)
