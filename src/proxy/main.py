"""FastAPI LLM Proxy 서버.

흐름 (Phase A):
  POST /v1/chat/completions  (OpenAI 호환)
  POST /api/chat             (Ollama 네이티브 — MindGraph LangChain4j용)
  POST /api/generate         (Ollama 네이티브)
    → _process_chat(request: ChatRequest) 공통 파이프라인
      → L1 Hash Cache 조회 (MD5 exact match)
        HIT → stream=True면 SSE wrapping, 아니면 그대로 반환 (quota 차감 없음)
      → L2 Semantic Cache 조회 (HNSW Vector Search + Validation)
        HIT → L1에도 저장 후 반환 (quota 차감 없음)
      → LLM 호출 경로
        → quota_tracker.check(user_id) → EXCEEDED면 HTTP 429
        → predict_output_tokens(query) → predicted_max
        → LLM_MODE=mock: _call_mock_llm()
        → LLM_MODE=ollama|openai: _llm_backend.chat() / .chat_stream()
    → latency_seconds 기록 / JSON Lines 로깅

환경변수:
  LLM_MODE:    'mock' | 'ollama' (기본: mock)
  LLM_BACKEND: 'ollama' | 'openai' (기본: ollama, LLM_MODE=ollama 시 사용)
  OLLAMA_BASE_URL:   Ollama 서버 URL (기본: http://localhost:11434/v1)
  OPENAI_API_KEY:    OpenAI API 키 (LLM_BACKEND=openai 시 필수)
  REDIS_URL:         Redis 연결 URL (기본: redis://localhost:6379)
  CACHE_TTL:         캐시 TTL 초 (기본: 86400)
  SEMANTIC_CACHE_ENABLED: L2 활성화 여부 (기본: true)
  SEMANTIC_THRESHOLD:     코사인 유사도 임계값 (기본: 0.75, Phase 2 실측 최적값)
  EMBEDDING_MODEL:        SentenceTransformer 모델명 (기본: paraphrase-multilingual-MiniLM-L12-v2)
  USER_QUOTA_TOKENS:      월간 토큰 할당량 (기본: 100000)
  COST_PER_INPUT_1K:      입력 토큰 1K당 USD (기본: 0.0005)
  COST_PER_OUTPUT_1K:     출력 토큰 1K당 USD (기본: 0.0015)
"""

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from prometheus_fastapi_instrumentator import Instrumentator
from redis.asyncio import Redis, from_url
from sentence_transformers import SentenceTransformer

from src.backends.base import LLMBackend
from src.backends.ollama_backend import OllamaBackend
from src.backends.openai_backend import OpenAIBackend
from src.metrics.prometheus import (
    api_calls_total,
    cache_hits_total,
    cost_saved_usd,
    latency_seconds,
    tokens_total,
    total_cost_usd,
)
from src.metrics.queue_metrics import queue_metrics
from src.proxy.cache.normalizer import normalize_query
from src.proxy.cache.redis_cache import RedisCache, cache_key
from src.proxy.cache.vector_cache import VectorCache
from src.proxy.cost.cost_calculator import CostCalculator
from src.proxy.cost.token_predictor import estimate_tokens, predict_output_tokens
from src.proxy.models import ChatRequest, ChatResponse
from src.proxy.rate_limit.quota_tracker import QuotaStatus, QuotaTracker
from src.proxy.validation.validator import SemanticValidator, extract_keywords

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "info").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

# 전역 상태 (lifespan에서 초기화)
_redis_client: Redis | None = None
_cache: RedisCache | None = None
_vector_cache: VectorCache | None = None
_embedding_model: SentenceTransformer | None = None
_validator: SemanticValidator | None = None
_quota_tracker: QuotaTracker | None = None
_cost_calculator: CostCalculator = CostCalculator()
_llm_backend: LLMBackend | None = None  # Phase A: 멀티 백엔드


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """앱 시작/종료 시 Redis 연결, 모델 초기화, 백엔드 초기화 관리."""
    global _redis_client, _cache, _vector_cache, _embedding_model
    global _validator, _quota_tracker, _llm_backend

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    ttl = int(os.getenv("CACHE_TTL", "86400"))
    semantic_enabled = os.getenv("SEMANTIC_CACHE_ENABLED", "true").lower() == "true"
    threshold = float(os.getenv("SEMANTIC_THRESHOLD", "0.75"))
    model_name = os.getenv("EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")
    quota = int(os.getenv("USER_QUOTA_TOKENS", "100000"))

    # L1 Hash Cache 초기화
    _redis_client = from_url(redis_url, encoding="utf-8", decode_responses=False)
    _cache = RedisCache(_redis_client, ttl=ttl)
    logger.info("Redis 연결 완료: %s", redis_url)

    # QuotaTracker 초기화
    _quota_tracker = QuotaTracker(_redis_client, quota=quota)
    logger.info("QuotaTracker 초기화 완료 (quota=%d tokens/month)", quota)

    if semantic_enabled:
        # L2 Vector Cache 초기화
        _vector_cache = VectorCache(_redis_client, ttl=ttl, threshold=threshold)
        await _vector_cache.ensure_index()

        # SentenceTransformer 로딩 (동기 함수 → to_thread로 이벤트 루프 블로킹 방지)
        logger.info("SentenceTransformer 로딩 중: %s ...", model_name)
        _embedding_model = await asyncio.to_thread(SentenceTransformer, model_name)
        logger.info("SentenceTransformer 로딩 완료 (threshold=%.2f)", threshold)

        _validator = SemanticValidator()
    else:
        logger.info("Semantic Cache 비활성화 (SEMANTIC_CACHE_ENABLED=false)")

    # Phase A: LLM 백엔드 초기화 (LLM_MODE=mock이면 불필요)
    llm_mode = os.getenv("LLM_MODE", "mock").lower()
    if llm_mode != "mock":
        backend_type = os.getenv("LLM_BACKEND", "ollama").lower()
        if backend_type == "openai":
            _llm_backend = OpenAIBackend(api_key=os.getenv("OPENAI_API_KEY", ""))
            logger.info("LLM 백엔드: OpenAI")
        else:
            base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
            api_key = os.getenv("OLLAMA_API_KEY", "ollama")
            _llm_backend = OllamaBackend(base_url=base_url, api_key=api_key)
            logger.info("LLM 백엔드: Ollama (%s)", base_url)
    else:
        logger.info("LLM 백엔드: Mock (LLM_MODE=mock)")

    # Phase 4: 큐 메트릭 수집 시작 (5초마다 이동평균 갱신)
    queue_metrics.start()
    logger.info("QueueMetricsCollector 시작 (sample_interval=5s)")

    yield

    queue_metrics.stop()
    await _redis_client.aclose()
    logger.info("Redis 연결 종료")


app = FastAPI(
    title="LLM-OPT Proxy",
    description="Semantic Caching + 트래픽 제어 기반 LLM 비용 최적화 프록시",
    version="0.4.0",
    lifespan=lifespan,
)

Instrumentator().instrument(app).expose(app)

# Phase A: Ollama 호환 라우터 등록 (/api/chat, /api/generate)
from src.proxy.routes.ollama_compat import ollama_router  # noqa: E402
app.include_router(ollama_router)


def _log_jsonl(event: dict) -> None:
    """JSON Lines 형식으로 요청/응답 로그를 기록한다."""
    logger.info(json.dumps(event, ensure_ascii=False))


async def _get_embedding(text: str) -> np.ndarray:
    """텍스트를 384차원 임베딩 벡터로 변환한다.

    SentenceTransformer.encode()는 동기 함수이므로
    asyncio.to_thread()로 이벤트 루프 블로킹을 방지한다.
    """
    if _embedding_model is None:
        raise RuntimeError("임베딩 모델이 초기화되지 않았습니다.")
    return await asyncio.to_thread(
        _embedding_model.encode, text, normalize_embeddings=True
    )


async def _detect_lang(text: str) -> str:
    """텍스트의 언어를 감지한다.

    langdetect는 동기 함수이므로 to_thread() 사용.
    감지 실패 시 빈 문자열 반환 (Validation에서 통과 처리).
    """
    try:
        from langdetect import detect
        return await asyncio.to_thread(detect, text)
    except Exception:
        return ""


async def _call_mock_llm(request: ChatRequest) -> str:
    """Mock LLM 응답 반환 (개발/테스트용).

    LLM_MODE=mock 환경에서 실제 LLM 호출 없이 고정 응답 반환.
    """
    last_message = request.messages[-1].content if request.messages else ""
    return f"[MOCK] '{last_message[:50]}...'에 대한 응답입니다. (model={request.model})"


async def _stream_cached_response(content: str) -> AsyncGenerator[str, None]:
    """캐시 히트 응답을 SSE 형식으로 wrapping하여 반환한다."""
    yield f"data: {json.dumps({'content': content, 'done': False}, ensure_ascii=False)}\n\n"
    yield f"data: {json.dumps({'content': '', 'done': True, 'truncated': False}, ensure_ascii=False)}\n\n"


async def _process_chat(request: ChatRequest) -> "ChatResponse | StreamingResponse":
    """L1→L2→LLM 캐시 파이프라인 핵심 로직.

    /v1/chat/completions와 /api/chat 양쪽에서 재사용한다.
    포맷 변환은 각 엔드포인트 핸들러에서 처리하고,
    이 함수는 ChatRequest를 받아 ChatResponse 또는 StreamingResponse를 반환한다.

    Args:
        request: ChatRequest (messages, model, stream, user_id)

    Returns:
        캐시 히트: ChatResponse(cached=True) 또는 StreamingResponse
        LLM 호출: ChatResponse(cached=False) 또는 StreamingResponse
    """
    if _cache is None:
        raise HTTPException(status_code=503, detail="캐시 초기화 중입니다.")

    start_time = time.perf_counter()
    request_id = str(uuid.uuid4())
    query_text = request.messages[-1].content if request.messages else ""
    messages_dict = [{"role": m.role, "content": m.content} for m in request.messages]

    # ── L1 Hash Cache 조회 ─────────────────────────────────────────────────
    l1_key = cache_key(messages_dict)
    cached_content = await _cache.get(l1_key)

    if cached_content is not None:
        elapsed = (time.perf_counter() - start_time) * 1000
        cache_hits_total.labels(tier="l1_hash").inc()
        latency_seconds.labels(cache_status="hit").observe(elapsed / 1000)
        _input_est = estimate_tokens(query_text)
        _output_est = predict_output_tokens(query_text)
        cost_saved_usd.labels(tier="l1_hash").inc(
            _cost_calculator.compute(_input_est, _output_est)
        )
        _log_jsonl({
            "request_id": request_id, "event": "cache_hit",
            "tier": "l1_hash", "latency_ms": round(elapsed, 2), "model": request.model,
            "user_id": request.user_id,
        })

        if request.stream:
            return StreamingResponse(
                _stream_cached_response(cached_content),
                media_type="text/event-stream",
            )
        return ChatResponse(
            id=request_id, content=cached_content,
            cached=True, latency_ms=round(elapsed, 2), tier="l1_hash",
            cost_usd=0.0,
        )

    # ── L2 Semantic Cache 조회 ─────────────────────────────────────────────
    embedding: np.ndarray | None = None

    if _vector_cache is not None and _embedding_model is not None:
        normalized_query = normalize_query(query_text)
        embedding = await _get_embedding(normalized_query)
        candidates = await _vector_cache.search(embedding)

        if candidates:
            query_lang = await _detect_lang(normalized_query)

            for cached_content, similarity, cached_meta in candidates:
                validation = _validator.validate(  # type: ignore[union-attr]
                    query_text=query_text,
                    query_lang=query_lang,
                    cached_metadata=cached_meta,
                    similarity=similarity,
                )

                if validation.passed:
                    elapsed = (time.perf_counter() - start_time) * 1000
                    cache_hits_total.labels(tier="l2_semantic").inc()
                    latency_seconds.labels(cache_status="hit").observe(elapsed / 1000)
                    _input_est = estimate_tokens(query_text)
                    _output_est = predict_output_tokens(query_text)
                    cost_saved_usd.labels(tier="l2_semantic").inc(
                        _cost_calculator.compute(_input_est, _output_est)
                    )
                    _log_jsonl({
                        "request_id": request_id, "event": "cache_hit",
                        "tier": "l2_semantic", "similarity": round(similarity, 4),
                        "latency_ms": round(elapsed, 2), "model": request.model,
                        "user_id": request.user_id,
                    })
                    await _cache.set(l1_key, cached_content)

                    if request.stream:
                        return StreamingResponse(
                            _stream_cached_response(cached_content),
                            media_type="text/event-stream",
                        )
                    return ChatResponse(
                        id=request_id, content=cached_content,
                        cached=True, latency_ms=round(elapsed, 2), tier="l2_semantic",
                        cost_usd=0.0,
                    )
                else:
                    logger.debug(
                        "Validation 실패 (sim=%.4f, 후보 %d개 중): %s",
                        similarity, len(candidates), validation.reason,
                    )

    # ── LLM 호출 경로 ──────────────────────────────────────────────────────
    if _quota_tracker is not None:
        quota_status = await _quota_tracker.check(request.user_id)
        if quota_status == QuotaStatus.EXCEEDED:
            raise HTTPException(
                status_code=429,
                detail=f"월간 토큰 할당량 초과. user_id={request.user_id}",
            )
        if quota_status == QuotaStatus.WARNING:
            logger.warning("할당량 80%% 이상 사용: user_id=%s", request.user_id)

    normalized_for_predict = normalize_query(query_text) if embedding is not None else query_text
    predicted_max = predict_output_tokens(query_text)
    input_tokens = estimate_tokens(normalized_for_predict)

    llm_mode = os.getenv("LLM_MODE", "mock").lower()

    # ── stream=True 경로 ────────────────────────────────────────────────────
    if request.stream:
        if llm_mode == "mock":
            queue_metrics.enter()
            try:
                mock_content = await _call_mock_llm(request)
            finally:
                queue_metrics.exit()

            async def _mock_stream() -> AsyncGenerator[str, None]:
                yield f"data: {json.dumps({'content': mock_content, 'done': False}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'content': '', 'done': True, 'truncated': False}, ensure_ascii=False)}\n\n"

            output_tokens = estimate_tokens(mock_content)
            total_tokens = input_tokens + output_tokens
            cost = _cost_calculator.compute(input_tokens, output_tokens)

            if _quota_tracker is not None:
                await _quota_tracker.increment(request.user_id, total_tokens)

            await _cache.set(l1_key, mock_content)
            if _vector_cache is not None and embedding is not None:
                query_lang = await _detect_lang(normalize_query(query_text))
                keywords = extract_keywords(query_text)
                await _vector_cache.store(
                    embedding=embedding, content=mock_content,
                    model=request.model, lang=query_lang, keywords=keywords,
                )

            elapsed = (time.perf_counter() - start_time) * 1000
            api_calls_total.inc()
            latency_seconds.labels(cache_status="miss").observe(elapsed / 1000)
            total_cost_usd.inc(cost)
            tokens_total.labels(type="input").inc(input_tokens)
            tokens_total.labels(type="output").inc(output_tokens)
            _log_jsonl({
                "request_id": request_id, "event": "llm_call_stream",
                "llm_mode": llm_mode, "latency_ms": round(elapsed, 2),
                "model": request.model, "user_id": request.user_id,
                "tokens_used": total_tokens, "cost_usd": cost,
            })
            return StreamingResponse(_mock_stream(), media_type="text/event-stream")
        else:
            # 백엔드 스트리밍 (queue_metrics는 스트리밍 완료 시 exit 불가 → 현재 깊이만 enter)
            if _llm_backend is None:
                raise HTTPException(status_code=503, detail="LLM 백엔드가 초기화되지 않았습니다.")
            queue_metrics.enter()
            api_calls_total.inc()
            return StreamingResponse(
                _llm_backend.chat_stream(messages_dict, request.model, predicted_max),
                media_type="text/event-stream",
            )

    # ── stream=False 경로 ───────────────────────────────────────────────────
    queue_metrics.enter()
    try:
        if llm_mode == "mock":
            content = await _call_mock_llm(request)
            output_tokens = estimate_tokens(content)
        else:
            if _llm_backend is None:
                raise HTTPException(status_code=503, detail="LLM 백엔드가 초기화되지 않았습니다.")
            try:
                llm_response = await _llm_backend.chat(messages_dict, request.model)
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"LLM 백엔드 오류: {e}") from e
            content = llm_response.content
            output_tokens = llm_response.output_tokens
            input_tokens = llm_response.input_tokens
    finally:
        queue_metrics.exit()

    total_tokens = input_tokens + output_tokens
    cost = _cost_calculator.compute(input_tokens, output_tokens)

    if _quota_tracker is not None:
        await _quota_tracker.increment(request.user_id, total_tokens)

    await _cache.set(l1_key, content)

    if _vector_cache is not None and embedding is not None:
        query_lang = await _detect_lang(normalize_query(query_text))
        keywords = extract_keywords(query_text)
        await _vector_cache.store(
            embedding=embedding,
            content=content,
            model=request.model,
            lang=query_lang,
            keywords=keywords,
        )

    elapsed = (time.perf_counter() - start_time) * 1000
    api_calls_total.inc()
    latency_seconds.labels(cache_status="miss").observe(elapsed / 1000)
    total_cost_usd.inc(cost)
    tokens_total.labels(type="input").inc(input_tokens)
    tokens_total.labels(type="output").inc(output_tokens)
    _log_jsonl({
        "request_id": request_id, "event": "llm_call",
        "llm_mode": llm_mode, "latency_ms": round(elapsed, 2),
        "model": request.model, "messages_count": len(request.messages),
        "user_id": request.user_id, "tokens_used": total_tokens, "cost_usd": cost,
    })

    return ChatResponse(
        id=request_id, content=content,
        cached=False, latency_ms=round(elapsed, 2),
        tokens_used=total_tokens, cost_usd=cost,
    )


@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(request: ChatRequest) -> "ChatResponse | StreamingResponse":
    """LLM 채팅 완성 엔드포인트 (OpenAI 호환).

    내부적으로 _process_chat()을 호출하여 L1→L2→LLM 파이프라인을 실행한다.
    """
    return await _process_chat(request)


@app.get("/health")
async def health() -> dict:
    """헬스체크 엔드포인트."""
    redis_ok = False
    if _redis_client is not None:
        try:
            await _redis_client.ping()
            redis_ok = True
        except Exception:
            pass

    backend_info: dict = {"type": os.getenv("LLM_BACKEND", "ollama")}
    if _llm_backend is not None:
        backend_info["connected"] = await _llm_backend.health_check()

    return {
        "status": "ok" if redis_ok else "degraded",
        "redis": redis_ok,
        "llm_mode": os.getenv("LLM_MODE", "mock"),
        "llm_backend": backend_info,
        "semantic_cache": {
            "enabled": _vector_cache is not None,
            "model_loaded": _embedding_model is not None,
            "threshold": os.getenv("SEMANTIC_THRESHOLD", "0.75"),
        },
        "quota": {
            "enabled": _quota_tracker is not None,
            "limit": _quota_tracker.quota if _quota_tracker is not None else None,
        },
        "queue_metrics": queue_metrics.get_snapshot(),
    }
