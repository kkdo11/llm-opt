"""OpenAI 호환 API 백엔드 구현 (Phase A-1).

Ollama가 기본 백엔드이며, 이 백엔드는 선택적 옵션이다.
환경변수: LLM_BACKEND=openai, OPENAI_API_KEY=sk-...

OpenAI 호환 API라면 base_url 지정으로 다른 서비스도 사용 가능.
예: Upstage Solar → base_url="https://api.upstage.ai/v1"

Ollama 백엔드와 다른 점:
  - base_url 미지정 시 OpenAI 공식 엔드포인트 사용
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
    """OpenAI API 호환 백엔드.

    OpenAI 공식 API 또는 OpenAI 호환 서비스(예: Upstage Solar)를 지원한다.

    Attributes:
        api_key: API 키 (필수)
        base_url: API 엔드포인트 URL (선택, None이면 OpenAI 공식 사용)
        default_model: 기본 모델명 (선택, None이면 요청 시 전달된 model 사용)
    """

    def __init__(self, api_key: str, base_url: str | None = None, default_model: str | None = None) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.default_model = default_model  # None이면 request.model 그대로 사용

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
        resolved_model = self.default_model or model

        try:
            response = await self._client.chat.completions.create(
                model=resolved_model,
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
        """OpenAI 호환 API 연결 상태를 확인한다.

        Returns:
            True이면 정상, False이면 연결 불가 (API 키 무효 포함)
        """
        try:
            await self._client.models.list()
            return True
        except Exception:
            return False
