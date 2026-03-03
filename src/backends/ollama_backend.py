"""Ollama 백엔드 구현 (Phase A-1).

Ollama는 OpenAI 호환 API(/v1/chat/completions)를 제공하므로
AsyncOpenAI 클라이언트를 그대로 사용한다.

기존 main.py의 _call_ollama, _call_ollama_streaming 로직을 이관.
"""

import json
import logging
from typing import AsyncGenerator

from openai import AsyncOpenAI

from src.backends.base import LLMBackend, LLMResponse
from src.proxy.cost.token_predictor import estimate_tokens

logger = logging.getLogger(__name__)


class OllamaBackend(LLMBackend):
    """Ollama LLM 백엔드.

    Ollama의 OpenAI 호환 엔드포인트(/v1/chat/completions)를 통해 호출.
    기본 모델: Qwen 2.5 14B (RTX 4080 Super, 로컬).

    Attributes:
        base_url: Ollama 서버 URL (기본: http://localhost:11434/v1)
        api_key: Ollama API 키 (기본값 "ollama", 실제로는 검증 안 함)
    """

    def __init__(self, base_url: str, api_key: str = "ollama") -> None:
        self.base_url = base_url
        self.api_key = api_key

    async def chat(self, messages: list[dict], model: str) -> LLMResponse:
        """Ollama에 단일 응답을 요청한다.

        Args:
            messages: [{"role": "user", "content": "..."}, ...] 형식
            model: 모델명 (예: "qwen2.5:14b")

        Returns:
            LLMResponse (content, input_tokens, output_tokens)

        Raises:
            Exception: Ollama 호출 실패 시
        """
        client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)

        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                stream=False,
            )
            content = response.choices[0].message.content or ""
            # 실측 토큰 수 (Ollama가 usage 반환 시) 또는 추정치
            input_tokens = (
                response.usage.prompt_tokens
                if response.usage
                else estimate_tokens(" ".join(m.get("content", "") for m in messages))
            )
            output_tokens = (
                response.usage.completion_tokens
                if response.usage
                else estimate_tokens(content)
            )
            return LLMResponse(
                content=content,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        except Exception as e:
            logger.error("Ollama 호출 실패: %s", e)
            raise

    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        predicted_max: int,
    ) -> AsyncGenerator[str, None]:
        """Ollama 스트리밍 호출. 예측 상한 150% 초과 시 중단.

        SSE 형식: data: {"content": "...", "done": false}\\n\\n

        Args:
            messages: 메시지 리스트
            model: 모델명
            predicted_max: 예측 최대 출력 토큰 수

        Yields:
            SSE 형식의 문자열 청크
        """
        client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)

        # 비용 폭탄 임계값: 예측 상한 × 1.5 (예상치; 실 Ollama 측정 미완)
        token_limit = int(predicted_max * 1.5)
        accumulated_text = ""
        truncated = False

        try:
            stream = await client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                stream=True,
            )

            async for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                if not delta:
                    continue

                accumulated_text += delta
                current_tokens = estimate_tokens(accumulated_text)

                if current_tokens > token_limit:
                    # 비용 폭탄 감지: 예측 상한의 150% 초과
                    truncated = True
                    yield f"data: {json.dumps({'content': delta + '[TRUNCATED]', 'done': False}, ensure_ascii=False)}\n\n"
                    logger.warning(
                        "스트리밍 중단: tokens=%d > limit=%d (predicted_max=%d)",
                        current_tokens,
                        token_limit,
                        predicted_max,
                    )
                    break

                yield f"data: {json.dumps({'content': delta, 'done': False}, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.error("Ollama 스트리밍 호출 실패: %s", e)
            yield f"data: {json.dumps({'error': str(e), 'done': True}, ensure_ascii=False)}\n\n"
            return

        yield f"data: {json.dumps({'content': '', 'done': True, 'truncated': truncated}, ensure_ascii=False)}\n\n"

    async def health_check(self) -> bool:
        """Ollama 서버 연결 상태를 확인한다.

        Returns:
            True이면 정상, False이면 연결 불가
        """
        client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)
        try:
            await client.models.list()
            return True
        except Exception:
            return False
