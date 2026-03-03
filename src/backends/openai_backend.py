"""OpenAI API 백엔드 구현 (Phase A-1).

Ollama가 기본 백엔드이며, 이 백엔드는 선택적 옵션이다.
환경변수: LLM_BACKEND=openai, OPENAI_API_KEY=sk-...

Ollama 백엔드와 다른 점:
  - OpenAI 공식 엔드포인트 사용 (base_url 없음)
  - 스트리밍 미구현 (현재 운영 환경은 Ollama 중심)
    TODO: 필요 시 chat_stream 구현
"""

import json
import logging
from typing import AsyncGenerator

from openai import AsyncOpenAI

from src.backends.base import LLMBackend, LLMResponse
from src.proxy.cost.token_predictor import estimate_tokens

logger = logging.getLogger(__name__)


class OpenAIBackend(LLMBackend):
    """OpenAI API 백엔드.

    Attributes:
        api_key: OpenAI API 키 (필수)
    """

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def chat(self, messages: list[dict], model: str) -> LLMResponse:
        """OpenAI API에 단일 응답을 요청한다.

        Args:
            messages: [{"role": "user", "content": "..."}, ...] 형식
            model: 모델명 (예: "gpt-4o-mini")

        Returns:
            LLMResponse (content, input_tokens, output_tokens)

        Raises:
            Exception: API 호출 실패 시
        """
        client = AsyncOpenAI(api_key=self.api_key)

        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                stream=False,
            )
            content = response.choices[0].message.content or ""
            # OpenAI는 usage 정보를 항상 반환
            input_tokens = response.usage.prompt_tokens if response.usage else estimate_tokens(
                " ".join(m.get("content", "") for m in messages)
            )
            output_tokens = response.usage.completion_tokens if response.usage else estimate_tokens(content)
            return LLMResponse(
                content=content,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        except Exception as e:
            logger.error("OpenAI 호출 실패: %s", e)
            raise

    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        predicted_max: int,
    ) -> AsyncGenerator[str, None]:
        """OpenAI API 스트리밍 호출.

        TODO: 현재 운영 환경은 Ollama 중심이므로 기본 구현만 제공.
              실제 OpenAI 스트리밍이 필요한 경우 OllamaBackend.chat_stream 참고하여 구현.
        """
        # 단일 응답을 SSE로 wrapping하여 반환 (임시 구현)
        result = await self.chat(messages, model)
        yield f"data: {json.dumps({'content': result.content, 'done': False}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'content': '', 'done': True, 'truncated': False}, ensure_ascii=False)}\n\n"

    async def health_check(self) -> bool:
        """OpenAI API 연결 상태를 확인한다.

        Returns:
            True이면 정상, False이면 연결 불가 (API 키 무효 포함)
        """
        client = AsyncOpenAI(api_key=self.api_key)
        try:
            await client.models.list()
            return True
        except Exception:
            return False
