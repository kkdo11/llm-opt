"""Ollama 네이티브 포맷 호환 엔드포인트 (Phase A-2).

MindGraph의 LangChain4j OllamaChatModel은 Ollama 네이티브 포맷을 사용한다:
  - POST /api/chat     (채팅 형식, 메시지 리스트)
  - POST /api/generate (텍스트 완성 형식, 단일 프롬프트)

이 모듈은 Ollama 포맷 ↔ 내부 ChatRequest/ChatResponse 변환만 담당한다.
캐시/검증/비용 파이프라인은 main._process_chat()을 그대로 재사용한다.

Ollama /api/chat 요청 포맷:
  {
    "model": "qwen2.5:14b",
    "messages": [{"role": "user", "content": "..."}],
    "stream": false,
    "options": {}
  }

Ollama /api/chat 응답 포맷 (non-stream):
  {
    "model": "qwen2.5:14b",
    "created_at": "2026-01-01T00:00:00Z",
    "message": {"role": "assistant", "content": "..."},
    "done": true,
    "total_duration": 0,
    "prompt_eval_count": 0,
    "eval_count": 0
  }

Ollama 스트리밍 포맷 (NDJSON, 기존 SSE와 다름):
  {"model":"...","message":{"role":"assistant","content":"청크"},"done":false}\\n
  {"model":"...","message":{"role":"assistant","content":""},"done":true,...}\\n
"""

import json
import logging
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.proxy.models import ChatMessage, ChatRequest, ChatResponse

logger = logging.getLogger(__name__)

ollama_router = APIRouter()


# ── Ollama 포맷 Pydantic 모델 ──────────────────────────────────────────────


class OllamaMessage(BaseModel):
    """Ollama 메시지 형식."""

    role: str
    content: str


class OllamaChatRequest(BaseModel):
    """POST /api/chat 요청 모델 (Ollama 네이티브 포맷).

    LangChain4j OllamaChatModel이 전송하는 형식.
    """

    model: str = "qwen2.5:14b"
    messages: list[OllamaMessage]
    stream: bool = False
    options: dict = {}
    user_id: str = "anonymous"  # LLM-OPT 확장 필드 (할당량 추적용)


class OllamaChatResponse(BaseModel):
    """POST /api/chat 응답 모델 (Ollama 네이티브 포맷, non-stream)."""

    model: str
    created_at: str
    message: OllamaMessage
    done: bool = True
    total_duration: int = 0      # 나노초 (실측 미완, 0으로 반환)
    prompt_eval_count: int = 0   # 입력 토큰 수 (있으면 채움)
    eval_count: int = 0          # 출력 토큰 수 (있으면 채움)


class OllamaGenerateRequest(BaseModel):
    """POST /api/generate 요청 모델 (Ollama 네이티브 포맷).

    단일 프롬프트 기반 텍스트 완성 형식.
    내부적으로 단일 user 메시지로 변환하여 처리한다.
    """

    model: str = "qwen2.5:14b"
    prompt: str
    stream: bool = False
    options: dict = {}
    user_id: str = "anonymous"


class OllamaGenerateResponse(BaseModel):
    """POST /api/generate 응답 모델 (Ollama 네이티브 포맷, non-stream)."""

    model: str
    created_at: str
    response: str
    done: bool = True
    total_duration: int = 0
    prompt_eval_count: int = 0
    eval_count: int = 0


# ── 변환 헬퍼 ─────────────────────────────────────────────────────────────


def _to_chat_request(
    model: str,
    messages: list[OllamaMessage],
    stream: bool,
    user_id: str,
) -> ChatRequest:
    """OllamaMessage 리스트를 내부 ChatRequest로 변환한다."""
    return ChatRequest(
        model=model,
        messages=[ChatMessage(role=m.role, content=m.content) for m in messages],
        stream=stream,
        user_id=user_id,
    )


def _chat_response_to_ollama(
    result: ChatResponse,
    model: str,
) -> OllamaChatResponse:
    """내부 ChatResponse를 Ollama 응답 포맷으로 변환한다."""
    return OllamaChatResponse(
        model=model,
        created_at=datetime.now(timezone.utc).isoformat(),
        message=OllamaMessage(role="assistant", content=result.content),
        done=True,
        prompt_eval_count=0,
        eval_count=result.tokens_used or 0,
    )


async def _sse_to_ndjson(
    sse_generator: AsyncGenerator[str, None],
    model: str,
) -> AsyncGenerator[str, None]:
    """기존 SSE 스트림을 Ollama NDJSON 스트림으로 변환한다.

    기존 SSE 포맷: data: {"content": "청크", "done": false}\\n\\n
    Ollama NDJSON: {"model":"...","message":{"role":"assistant","content":"청크"},"done":false}\\n

    Args:
        sse_generator: main._process_chat()이 반환한 SSE StreamingResponse의 body generator
        model: 응답에 포함할 모델명

    Yields:
        Ollama NDJSON 형식의 문자열 (줄 단위)
    """
    async for chunk in sse_generator:
        # SSE 포맷: "data: {...}\n\n"
        if not chunk.startswith("data: "):
            continue
        raw = chunk[len("data: "):].strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        content = data.get("content", "")
        done = data.get("done", False)

        ndjson_chunk = {
            "model": model,
            "message": {"role": "assistant", "content": content},
            "done": done,
        }
        if done:
            ndjson_chunk["total_duration"] = 0

        yield json.dumps(ndjson_chunk, ensure_ascii=False) + "\n"


# ── 엔드포인트 ────────────────────────────────────────────────────────────


@ollama_router.post("/api/chat", response_model=None)
async def ollama_chat(
    request: OllamaChatRequest,
) -> "OllamaChatResponse | StreamingResponse":
    """Ollama 네이티브 /api/chat 엔드포인트.

    MindGraph LangChain4j OllamaChatModel이 호출하는 경로.
    내부적으로 기존 L1→L2→LLM 파이프라인(_process_chat)을 재사용하고,
    응답을 Ollama 포맷으로 변환하여 반환한다.

    Args:
        request: OllamaChatRequest (Ollama 네이티브 포맷)

    Returns:
        non-stream: OllamaChatResponse
        stream: NDJSON StreamingResponse
    """
    # 지연 import: main과 ollama_compat의 순환 import 방지
    from src.proxy.main import _process_chat

    chat_req = _to_chat_request(
        model=request.model,
        messages=request.messages,
        stream=request.stream,
        user_id=request.user_id,
    )

    result = await _process_chat(chat_req)

    # non-stream 응답
    if isinstance(result, ChatResponse):
        return _chat_response_to_ollama(result, request.model)

    # stream 응답: SSE → NDJSON 변환
    # result는 StreamingResponse이므로 body_iterator를 꺼내 변환
    return StreamingResponse(
        _sse_to_ndjson(result.body_iterator, request.model),
        media_type="application/x-ndjson",
    )


@ollama_router.post("/api/generate", response_model=None)
async def ollama_generate(
    request: OllamaGenerateRequest,
) -> "OllamaGenerateResponse | StreamingResponse":
    """Ollama 네이티브 /api/generate 엔드포인트.

    단일 프롬프트를 user 메시지로 변환하여 파이프라인에 전달한다.

    Args:
        request: OllamaGenerateRequest (prompt 문자열)

    Returns:
        non-stream: OllamaGenerateResponse
        stream: NDJSON StreamingResponse
    """
    from src.proxy.main import _process_chat

    chat_req = _to_chat_request(
        model=request.model,
        messages=[OllamaMessage(role="user", content=request.prompt)],
        stream=request.stream,
        user_id=request.user_id,
    )

    result = await _process_chat(chat_req)

    if isinstance(result, ChatResponse):
        return OllamaGenerateResponse(
            model=request.model,
            created_at=datetime.now(timezone.utc).isoformat(),
            response=result.content,
            done=True,
            eval_count=result.tokens_used or 0,
        )

    # stream: NDJSON 변환 (generate 포맷 — "response" 필드 사용)
    async def _generate_ndjson(
        sse_gen: AsyncGenerator[str, None],
    ) -> AsyncGenerator[str, None]:
        async for chunk in sse_gen:
            if not chunk.startswith("data: "):
                continue
            raw = chunk[len("data: "):].strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            done = data.get("done", False)
            ndjson_chunk = {
                "model": request.model,
                "response": data.get("content", ""),
                "done": done,
            }
            yield json.dumps(ndjson_chunk, ensure_ascii=False) + "\n"

    return StreamingResponse(
        _generate_ndjson(result.body_iterator),
        media_type="application/x-ndjson",
    )
