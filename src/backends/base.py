"""LLM 백엔드 추상화 기반 클래스 (Phase A-1).

캐시/검증/비용 레이어는 백엔드와 무관하게 동작한다.
백엔드는 main.py에서 LLM_BACKEND 환경변수로 주입(DI)된다.

설계 원칙:
  - OllamaBackend가 기본값. OpenAIBackend는 선택적 옵션.
  - chat()은 단일 응답 반환, chat_stream()은 SSE 청크 제너레이터 반환.
  - 백엔드 오류는 HTTPException으로 감싸지 않고 상위로 전파 (main.py에서 처리).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncGenerator


@dataclass
class LLMResponse:
    """LLM 단일 응답.

    Attributes:
        content: 응답 텍스트
        input_tokens: 입력 토큰 수 (추정치 또는 실측치)
        output_tokens: 출력 토큰 수 (추정치 또는 실측치)
    """

    content: str
    input_tokens: int
    output_tokens: int


class LLMBackend(ABC):
    """LLM 백엔드 추상 기반 클래스.

    모든 백엔드는 이 인터페이스를 구현해야 한다.
    캐시/검증/비용 레이어는 이 인터페이스만 알고,
    실제 LLM 제공자(Ollama, OpenAI 등)는 모른다.
    """

    @abstractmethod
    async def chat(self, messages: list[dict], model: str) -> LLMResponse:
        """단일 LLM 응답을 반환한다.

        Args:
            messages: [{"role": "user", "content": "..."}, ...] 형식의 메시지 리스트
            model: 모델명 (예: "qwen2.5:14b", "gpt-4o-mini")

        Returns:
            LLMResponse (content, input_tokens, output_tokens)

        Raises:
            Exception: 백엔드 호출 실패 시 (main.py에서 HTTPException으로 변환)
        """
        ...

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        predicted_max: int,
    ) -> AsyncGenerator[str, None]:
        """SSE 형식 스트리밍 응답을 반환한다.

        Args:
            messages: 메시지 리스트
            model: 모델명
            predicted_max: 예측 최대 출력 토큰 수 (150% 초과 시 중단)

        Yields:
            SSE 형식의 문자열: data: {"content": "...", "done": false}\\n\\n
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """백엔드 연결 상태를 확인한다.

        Returns:
            True이면 정상, False이면 연결 불가
        """
        ...
