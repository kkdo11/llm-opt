"""LLM 백엔드 단위 테스트 (Phase A-1).

외부 의존성(OpenAI SDK, Ollama)은 모두 mock으로 처리한다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.backends.base import LLMResponse
from src.backends.ollama_backend import OllamaBackend
from src.backends.openai_backend import OpenAIBackend


# ---------------------------------------------------------------------------
# OllamaBackend
# ---------------------------------------------------------------------------


class TestOllamaBackendChat:
    """OllamaBackend.chat() 테스트."""

    @pytest.mark.asyncio
    async def test_chat_returns_llm_response(self) -> None:
        """정상 응답 시 LLMResponse를 반환해야 한다."""
        backend = OllamaBackend(base_url="http://localhost:11434/v1")

        mock_response = MagicMock()
        mock_response.choices[0].message.content = "안녕하세요!"
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("src.backends.ollama_backend.AsyncOpenAI", return_value=mock_client):
            result = await backend.chat(
                messages=[{"role": "user", "content": "안녕"}],
                model="qwen2.5:14b",
            )

        assert isinstance(result, LLMResponse)
        assert result.content == "안녕하세요!"
        assert result.input_tokens == 10
        assert result.output_tokens == 5

    @pytest.mark.asyncio
    async def test_chat_uses_estimated_tokens_when_no_usage(self) -> None:
        """usage 정보가 없으면 추정치를 사용해야 한다."""
        backend = OllamaBackend(base_url="http://localhost:11434/v1")

        mock_response = MagicMock()
        mock_response.choices[0].message.content = "응답"
        mock_response.usage = None  # usage 없음

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("src.backends.ollama_backend.AsyncOpenAI", return_value=mock_client):
            result = await backend.chat(
                messages=[{"role": "user", "content": "질문"}],
                model="qwen2.5:14b",
            )

        assert result.input_tokens > 0
        assert result.output_tokens > 0

    @pytest.mark.asyncio
    async def test_chat_raises_on_api_error(self) -> None:
        """API 오류 시 예외를 전파해야 한다."""
        backend = OllamaBackend(base_url="http://localhost:11434/v1")

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=Exception("Connection refused")
        )

        with patch("src.backends.ollama_backend.AsyncOpenAI", return_value=mock_client):
            with pytest.raises(Exception, match="Connection refused"):
                await backend.chat(
                    messages=[{"role": "user", "content": "질문"}],
                    model="qwen2.5:14b",
                )


class TestOllamaBackendChatStream:
    """OllamaBackend.chat_stream() 테스트."""

    @pytest.mark.asyncio
    async def test_stream_yields_sse_chunks(self) -> None:
        """정상 스트리밍 시 SSE 형식 청크를 반환해야 한다."""
        backend = OllamaBackend(base_url="http://localhost:11434/v1")

        def _make_chunk(content: str) -> MagicMock:
            chunk = MagicMock()
            chunk.choices[0].delta.content = content
            return chunk

        mock_stream = MagicMock()
        mock_stream.__aiter__ = AsyncMock(
            return_value=iter([_make_chunk("안녕"), _make_chunk("하세요")])
        )

        async def _async_iter():
            for c in [_make_chunk("안녕"), _make_chunk("하세요")]:
                yield c

        mock_stream.__aiter__ = lambda self: _async_iter().__aiter__()

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_stream)

        with patch("src.backends.ollama_backend.AsyncOpenAI", return_value=mock_client):
            chunks = []
            async for chunk in backend.chat_stream(
                messages=[{"role": "user", "content": "안녕"}],
                model="qwen2.5:14b",
                predicted_max=100,
            ):
                chunks.append(chunk)

        # SSE 형식 확인
        assert any("안녕" in c for c in chunks)
        # 마지막 청크: done=true
        import json
        last = json.loads(chunks[-1].replace("data: ", "").strip())
        assert last["done"] is True

    @pytest.mark.asyncio
    async def test_stream_truncates_on_token_overflow(self) -> None:
        """예측 상한 150% 초과 시 [TRUNCATED]를 포함하고 중단해야 한다."""
        backend = OllamaBackend(base_url="http://localhost:11434/v1")

        # 매우 긴 내용으로 토큰 초과 유도
        long_content = "a" * 1000

        def _make_chunk(content: str) -> MagicMock:
            chunk = MagicMock()
            chunk.choices[0].delta.content = content
            return chunk

        async def _async_iter():
            yield _make_chunk(long_content)

        mock_stream = MagicMock()
        mock_stream.__aiter__ = lambda self: _async_iter().__aiter__()

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_stream)

        with patch("src.backends.ollama_backend.AsyncOpenAI", return_value=mock_client):
            chunks = []
            async for chunk in backend.chat_stream(
                messages=[{"role": "user", "content": "짧은 질문"}],
                model="qwen2.5:14b",
                predicted_max=1,  # 매우 낮은 임계값 → 즉시 초과
            ):
                chunks.append(chunk)

        assert any("[TRUNCATED]" in c for c in chunks)


class TestOllamaBackendHealthCheck:
    """OllamaBackend.health_check() 테스트."""

    @pytest.mark.asyncio
    async def test_health_check_returns_true_when_connected(self) -> None:
        """Ollama 정상 연결 시 True를 반환해야 한다."""
        backend = OllamaBackend(base_url="http://localhost:11434/v1")

        mock_client = AsyncMock()
        mock_client.models.list = AsyncMock(return_value=MagicMock())

        with patch("src.backends.ollama_backend.AsyncOpenAI", return_value=mock_client):
            result = await backend.health_check()

        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_when_disconnected(self) -> None:
        """연결 실패 시 False를 반환해야 한다."""
        backend = OllamaBackend(base_url="http://localhost:11434/v1")

        mock_client = AsyncMock()
        mock_client.models.list = AsyncMock(side_effect=Exception("Connection refused"))

        with patch("src.backends.ollama_backend.AsyncOpenAI", return_value=mock_client):
            result = await backend.health_check()

        assert result is False


# ---------------------------------------------------------------------------
# OpenAIBackend
# ---------------------------------------------------------------------------


class TestOpenAIBackendChat:
    """OpenAIBackend.chat() 테스트."""

    @pytest.mark.asyncio
    async def test_chat_returns_llm_response(self) -> None:
        """정상 응답 시 LLMResponse를 반환해야 한다."""
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "Hello!"
        mock_response.usage.prompt_tokens = 8
        mock_response.usage.completion_tokens = 3

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("src.backends.openai_backend.AsyncOpenAI", return_value=mock_client):
            backend = OpenAIBackend(api_key="sk-test")
            result = await backend.chat(
                messages=[{"role": "user", "content": "Hi"}],
                model="gpt-4o-mini",
            )

        assert result.content == "Hello!"
        assert result.input_tokens == 8
        assert result.output_tokens == 3

    @pytest.mark.asyncio
    async def test_chat_raises_on_api_error(self) -> None:
        """API 오류 시 예외를 전파해야 한다."""
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=Exception("Invalid API key")
        )

        with patch("src.backends.openai_backend.AsyncOpenAI", return_value=mock_client):
            backend = OpenAIBackend(api_key="sk-invalid")
            with pytest.raises(Exception, match="Invalid API key"):
                await backend.chat(
                    messages=[{"role": "user", "content": "Hi"}],
                    model="gpt-4o-mini",
                )

    @pytest.mark.asyncio
    async def test_health_check_returns_true_when_connected(self) -> None:
        """OpenAI API 정상 연결 시 True를 반환해야 한다."""
        mock_client = AsyncMock()
        mock_client.models.list = AsyncMock(return_value=MagicMock())

        with patch("src.backends.openai_backend.AsyncOpenAI", return_value=mock_client):
            backend = OpenAIBackend(api_key="sk-test")
            result = await backend.health_check()

        assert result is True
