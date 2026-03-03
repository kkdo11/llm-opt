"""Ollama 호환 엔드포인트 단위 테스트 (Phase A-2).

/api/chat, /api/generate 엔드포인트를 검증한다.
Redis, LLM은 mock으로 처리 (LLM_MODE=mock, SEMANTIC_CACHE_ENABLED=false).

테스트 방식:
  - 비동기 테스트: AsyncClient + ASGITransport (lifespan 없이 직접 mock 주입)
  - 스트리밍 테스트: TestClient.stream() (lifespan 후 mock 재주입 필요)
"""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from src.proxy.cache.redis_cache import RedisCache
from src.proxy.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_cache() -> MagicMock:
    """RedisCache mock 객체."""
    import src.proxy.main as proxy_main

    proxy_main._quota_tracker = None  # lifespan 잔류 상태 격리
    cache = MagicMock(spec=RedisCache)
    cache.get = AsyncMock(return_value=None)  # 기본: 캐시 미스
    cache.set = AsyncMock()
    return cache


# ---------------------------------------------------------------------------
# POST /api/chat — non-stream (AsyncClient + ASGITransport)
# ---------------------------------------------------------------------------


class TestOllamaChatEndpoint:
    """POST /api/chat 엔드포인트 테스트 (비동기, lifespan 없음)."""

    @pytest.mark.asyncio
    async def test_chat_miss_returns_ollama_format(
        self, mock_cache: MagicMock
    ) -> None:
        """캐시 미스 시 Ollama 응답 포맷으로 반환해야 한다."""
        import src.proxy.main as proxy_main

        proxy_main._cache = mock_cache

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.dict(
                os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "false"}
            ):
                response = await client.post(
                    "/api/chat",
                    json={
                        "model": "qwen2.5:14b",
                        "messages": [{"role": "user", "content": "안녕하세요"}],
                        "stream": False,
                    },
                )

        assert response.status_code == 200
        data = response.json()
        assert "model" in data
        assert "message" in data
        assert data["message"]["role"] == "assistant"
        assert isinstance(data["message"]["content"], str)
        assert len(data["message"]["content"]) > 0
        assert data["done"] is True
        assert "created_at" in data

    @pytest.mark.asyncio
    async def test_chat_l1_cache_hit_returns_ollama_format(
        self, mock_cache: MagicMock
    ) -> None:
        """L1 캐시 히트 시에도 Ollama 포맷으로 반환해야 한다."""
        import src.proxy.main as proxy_main

        cached_text = "캐시된 LLM 응답입니다."
        mock_cache.get = AsyncMock(return_value=cached_text.encode("utf-8"))
        proxy_main._cache = mock_cache

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.dict(
                os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "false"}
            ):
                response = await client.post(
                    "/api/chat",
                    json={
                        "model": "qwen2.5:14b",
                        "messages": [{"role": "user", "content": "안녕하세요"}],
                        "stream": False,
                    },
                )

        assert response.status_code == 200
        data = response.json()
        assert data["message"]["content"] == cached_text
        assert data["done"] is True

    @pytest.mark.asyncio
    async def test_chat_model_field_preserved(
        self, mock_cache: MagicMock
    ) -> None:
        """요청한 모델명이 응답에 그대로 포함되어야 한다."""
        import src.proxy.main as proxy_main

        proxy_main._cache = mock_cache

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.dict(
                os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "false"}
            ):
                response = await client.post(
                    "/api/chat",
                    json={
                        "model": "qwen2.5:14b",
                        "messages": [{"role": "user", "content": "테스트"}],
                    },
                )

        assert response.status_code == 200
        assert response.json()["model"] == "qwen2.5:14b"

    @pytest.mark.asyncio
    async def test_chat_missing_messages_returns_422(
        self, mock_cache: MagicMock
    ) -> None:
        """messages 필드 누락 시 422를 반환해야 한다."""
        import src.proxy.main as proxy_main

        proxy_main._cache = mock_cache

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/chat",
                json={"model": "qwen2.5:14b"},
            )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_chat_with_multi_turn_messages(
        self, mock_cache: MagicMock
    ) -> None:
        """멀티 턴 메시지도 처리할 수 있어야 한다."""
        import src.proxy.main as proxy_main

        proxy_main._cache = mock_cache

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.dict(
                os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "false"}
            ):
                response = await client.post(
                    "/api/chat",
                    json={
                        "model": "qwen2.5:14b",
                        "messages": [
                            {"role": "user", "content": "파이썬이 뭐야?"},
                            {"role": "assistant", "content": "파이썬은 프로그래밍 언어입니다."},
                            {"role": "user", "content": "그럼 자바는?"},
                        ],
                    },
                )

        assert response.status_code == 200
        assert response.json()["done"] is True


# ---------------------------------------------------------------------------
# POST /api/chat — stream=True (TestClient.stream())
# ---------------------------------------------------------------------------


class TestOllamaChatStreamEndpoint:
    """POST /api/chat (stream=True) 엔드포인트 테스트."""

    def test_stream_returns_ndjson_content_type(
        self, mock_cache: MagicMock
    ) -> None:
        """stream=True 시 NDJSON Content-Type을 반환해야 한다."""
        import src.proxy.main as proxy_main

        with patch.dict(
            os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "false"}
        ):
            with TestClient(app) as client:
                # lifespan 완료 후 mock 재주입
                proxy_main._cache = mock_cache
                proxy_main._quota_tracker = None

                with client.stream(
                    "POST",
                    "/api/chat",
                    json={
                        "model": "qwen2.5:14b",
                        "messages": [{"role": "user", "content": "안녕"}],
                        "stream": True,
                    },
                ) as response:
                    assert response.status_code == 200
                    assert "ndjson" in response.headers.get("content-type", "")

    def test_stream_last_chunk_has_done_true(
        self, mock_cache: MagicMock
    ) -> None:
        """스트림의 마지막 청크에 done=true가 포함되어야 한다."""
        import src.proxy.main as proxy_main

        chunks = []
        with patch.dict(
            os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "false"}
        ):
            with TestClient(app) as client:
                proxy_main._cache = mock_cache
                proxy_main._quota_tracker = None

                with client.stream(
                    "POST",
                    "/api/chat",
                    json={
                        "model": "qwen2.5:14b",
                        "messages": [{"role": "user", "content": "안녕"}],
                        "stream": True,
                    },
                ) as response:
                    for line in response.iter_lines():
                        if line.strip():
                            chunks.append(json.loads(line))

        assert len(chunks) > 0
        last_chunk = chunks[-1]
        assert last_chunk["done"] is True
        assert "model" in last_chunk
        assert "message" in last_chunk

    def test_stream_chunks_have_assistant_role(
        self, mock_cache: MagicMock
    ) -> None:
        """스트림 청크의 message.role이 assistant여야 한다."""
        import src.proxy.main as proxy_main

        chunks = []
        with patch.dict(
            os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "false"}
        ):
            with TestClient(app) as client:
                proxy_main._cache = mock_cache
                proxy_main._quota_tracker = None

                with client.stream(
                    "POST",
                    "/api/chat",
                    json={
                        "model": "qwen2.5:14b",
                        "messages": [{"role": "user", "content": "안녕"}],
                        "stream": True,
                    },
                ) as response:
                    for line in response.iter_lines():
                        if line.strip():
                            chunks.append(json.loads(line))

        for chunk in chunks:
            assert chunk["message"]["role"] == "assistant"


# ---------------------------------------------------------------------------
# POST /api/generate
# ---------------------------------------------------------------------------


class TestOllamaGenerateEndpoint:
    """POST /api/generate 엔드포인트 테스트."""

    @pytest.mark.asyncio
    async def test_generate_returns_response_field(
        self, mock_cache: MagicMock
    ) -> None:
        """응답에 'response' 필드가 포함되어야 한다."""
        import src.proxy.main as proxy_main

        proxy_main._cache = mock_cache

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.dict(
                os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "false"}
            ):
                response = await client.post(
                    "/api/generate",
                    json={"model": "qwen2.5:14b", "prompt": "안녕하세요"},
                )

        assert response.status_code == 200
        data = response.json()
        assert "response" in data
        assert isinstance(data["response"], str)
        assert data["done"] is True
        assert "model" in data

    @pytest.mark.asyncio
    async def test_generate_missing_prompt_returns_422(
        self, mock_cache: MagicMock
    ) -> None:
        """prompt 필드 누락 시 422를 반환해야 한다."""
        import src.proxy.main as proxy_main

        proxy_main._cache = mock_cache

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/generate",
                json={"model": "qwen2.5:14b"},
            )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_generate_with_cache_hit(
        self, mock_cache: MagicMock
    ) -> None:
        """캐시 히트 시에도 generate 응답 포맷을 반환해야 한다."""
        import src.proxy.main as proxy_main

        cached_text = "캐시 응답입니다."
        mock_cache.get = AsyncMock(return_value=cached_text.encode("utf-8"))
        proxy_main._cache = mock_cache

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.dict(
                os.environ, {"LLM_MODE": "mock", "SEMANTIC_CACHE_ENABLED": "false"}
            ):
                response = await client.post(
                    "/api/generate",
                    json={"model": "qwen2.5:14b", "prompt": "안녕하세요"},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["response"] == cached_text
        assert data["done"] is True
