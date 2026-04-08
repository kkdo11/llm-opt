"""Pydantic 요청/응답 모델 정의."""

from pydantic import BaseModel, field_validator


class ChatMessage(BaseModel):
    """OpenAI 호환 채팅 메시지."""

    role: str
    content: str

    @field_validator("role")
    @classmethod
    def normalize_role(cls, v: str) -> str:
        return v.lower()


class ChatRequest(BaseModel):
    """POST /v1/chat/completions 요청 모델."""

    model: str = "qwen2.5:14b"
    messages: list[ChatMessage]
    stream: bool = False
    user_id: str = "anonymous"


class ChatResponse(BaseModel):
    """프록시 응답 모델.

    cached=True이면 Redis에서 반환된 캐시 히트 응답.
    latency_ms는 프록시 전체 처리 시간 (캐시 조회 포함).
    tier: 캐시 티어 ('l1_hash' | 'l2_semantic' | None)
    tokens_used: 이번 요청에서 소비한 토큰 수 (입력+출력, 캐시 히트 시 None)
    cost_usd: 이번 요청 비용 USD (캐시 히트 시 0.0)
    """

    id: str
    content: str
    cached: bool
    latency_ms: float
    tier: str | None = None
    tokens_used: int | None = None
    cost_usd: float | None = None
