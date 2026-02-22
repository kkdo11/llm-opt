"""FastAPI LLM Proxy 서버.

흐름 (Phase 2):
  POST /v1/chat/completions
    → L1 Hash Cache 조회 (MD5 exact match)
      HIT → 즉시 반환
    → L2 Semantic Cache 조회 (HNSW Vector Search + Validation)
      HIT → L1에도 저장 후 반환
    → LLM 호출 (mock or Ollama)
      → L1 + L2 모두 저장
    → latency_seconds 기록 / JSON Lines 로깅

환경변수:
  LLM_MODE: 'mock' | 'ollama' (기본: mock)
  OLLAMA_BASE_URL: Ollama 서버 URL (기본: http://localhost:11434/v1)
  REDIS_URL: Redis 연결 URL (기본: redis://localhost:6379)
  CACHE_TTL: 캐시 TTL 초 (기본: 86400)
  SEMANTIC_CACHE_ENABLED: L2 활성화 여부 (기본: true)
  SEMANTIC_THRESHOLD: 코사인 유사도 임계값 (기본: 0.85)
  EMBEDDING_MODEL: SentenceTransformer 모델명 (기본: all-MiniLM-L6-v2)
"""

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncGenerator

import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from redis.asyncio import Redis, from_url
from sentence_transformers import SentenceTransformer

from src.metrics.prometheus import api_calls_total, cache_hits_total, latency_seconds
from src.proxy.cache.normalizer import normalize_query
from src.proxy.cache.redis_cache import RedisCache, cache_key
from src.proxy.cache.vector_cache import VectorCache
from src.proxy.models import ChatRequest, ChatResponse
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """앱 시작/종료 시 Redis 연결 및 모델 초기화 관리."""
    global _redis_client, _cache, _vector_cache, _embedding_model, _validator

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    ttl = int(os.getenv("CACHE_TTL", "86400"))
    semantic_enabled = os.getenv("SEMANTIC_CACHE_ENABLED", "true").lower() == "true"
    threshold = float(os.getenv("SEMANTIC_THRESHOLD", "0.85"))
    model_name = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

    # L1 Hash Cache 초기화
    _redis_client = from_url(redis_url, encoding="utf-8", decode_responses=False)
    _cache = RedisCache(_redis_client, ttl=ttl)
    logger.info("Redis 연결 완료: %s", redis_url)

    if semantic_enabled:
        # L2 Vector Cache 초기화
        _vector_cache = VectorCache(_redis_client, ttl=ttl, threshold=threshold)
        await _vector_cache.ensure_index()

        # SentenceTransformer 로딩 (동기 함수 → to_thread로 이벤트 루프 블로킹 방지)
        # 이유: 모델 로딩은 최초 1회 수초 소요. 요청 처리 중 초기화 불가.
        logger.info("SentenceTransformer 로딩 중: %s ...", model_name)
        _embedding_model = await asyncio.to_thread(SentenceTransformer, model_name)
        logger.info("SentenceTransformer 로딩 완료 (threshold=%.2f)", threshold)

        _validator = SemanticValidator()
    else:
        logger.info("Semantic Cache 비활성화 (SEMANTIC_CACHE_ENABLED=false)")

    yield

    await _redis_client.aclose()
    logger.info("Redis 연결 종료")


app = FastAPI(
    title="LLM-OPT Proxy",
    description="Semantic Caching + 트래픽 제어 기반 LLM 비용 최적화 프록시",
    version="0.2.0",
    lifespan=lifespan,
)

Instrumentator().instrument(app).expose(app)


def _log_jsonl(event: dict) -> None:
    """JSON Lines 형식으로 요청/응답 로그를 기록한다."""
    logger.info(json.dumps(event, ensure_ascii=False))


async def _get_embedding(text: str) -> np.ndarray:
    """텍스트를 384차원 임베딩 벡터로 변환한다.

    SentenceTransformer.encode()는 동기 함수이므로
    asyncio.to_thread()로 이벤트 루프 블로킹을 방지한다.

    Args:
        text: 임베딩할 텍스트

    Returns:
        numpy float32 배열 (shape: [384])
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

    Args:
        text: 언어를 감지할 텍스트

    Returns:
        언어 코드 (예: 'ko', 'en'), 실패 시 빈 문자열
    """
    try:
        from langdetect import detect
        return await asyncio.to_thread(detect, text)
    except Exception:
        return ""


async def _call_mock_llm(request: ChatRequest) -> str:
    """Mock LLM 응답 반환 (개발/테스트용).

    LLM_MODE=mock 환경에서 실제 Ollama 호출 없이 고정 응답 반환.
    """
    last_message = request.messages[-1].content if request.messages else ""
    return f"[MOCK] '{last_message[:50]}...'에 대한 응답입니다. (model={request.model})"


async def _call_ollama(request: ChatRequest) -> str:
    """Ollama LLM 호출 (실제 운영 모드).

    Raises:
        HTTPException: Ollama 호출 실패 시
    """
    from openai import AsyncOpenAI

    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    api_key = os.getenv("OLLAMA_API_KEY", "ollama")
    client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    try:
        response = await client.chat.completions.create(
            model=request.model,
            messages=[{"role": m.role, "content": m.content} for m in request.messages],
            stream=False,
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.error("Ollama 호출 실패: %s", e)
        raise HTTPException(status_code=502, detail=f"LLM 백엔드 오류: {e}") from e


@app.post("/v1/chat/completions", response_model=ChatResponse)
async def chat_completions(request: ChatRequest) -> ChatResponse:
    """LLM 채팅 완성 엔드포인트 (OpenAI 호환).

    L1 Hash Cache → L2 Semantic Cache → LLM 호출 순서로 처리한다.
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
        _log_jsonl({
            "request_id": request_id, "event": "cache_hit",
            "tier": "l1_hash", "latency_ms": round(elapsed, 2), "model": request.model,
        })
        return ChatResponse(
            id=request_id, content=cached_content,
            cached=True, latency_ms=round(elapsed, 2), tier="l1_hash",
        )

    # ── L2 Semantic Cache 조회 ─────────────────────────────────────────────
    # 임베딩을 1회만 계산하여 L2 검색과 저장 모두에 재사용
    embedding: np.ndarray | None = None

    if _vector_cache is not None and _embedding_model is not None:
        # 정규화: 영어↔한글 혼용 기술 용어를 통일하여 임베딩 유사도 향상
        # 예: "파이썬 list와 tuple" → "파이썬 리스트와 튜플"
        # 저장 시와 검색 시 모두 동일하게 적용해야 유사도 매칭이 성립
        normalized_query = normalize_query(query_text)
        embedding = await _get_embedding(normalized_query)
        candidates = await _vector_cache.search(embedding)

        if candidates:
            # 정규화 후 langdetect: 영어 기술 용어("list", "tuple")가 포함되면
            # langdetect가 오인식(et/vi 등)함 → 정규화로 한글로 변환 후 감지
            # 예: "파이썬 list와 tuple" → langdetect='et' (오인식) → 정규화 후 'ko'
            query_lang = await _detect_lang(normalized_query)

            # KNN k=3 후보를 유사도 내림차순으로 순회 — Validation 통과 첫 번째 선택
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
                    _log_jsonl({
                        "request_id": request_id, "event": "cache_hit",
                        "tier": "l2_semantic", "similarity": round(similarity, 4),
                        "latency_ms": round(elapsed, 2), "model": request.model,
                    })
                    # L1에도 저장: 동일 질문 재요청 시 L1 히트로 처리
                    await _cache.set(l1_key, cached_content)
                    return ChatResponse(
                        id=request_id, content=cached_content,
                        cached=True, latency_ms=round(elapsed, 2), tier="l2_semantic",
                    )
                else:
                    logger.debug(
                        "Validation 실패 (sim=%.4f, 후보 %d개 중): %s",
                        similarity, len(candidates), validation.reason,
                    )

    # ── LLM 호출 ───────────────────────────────────────────────────────────
    llm_mode = os.getenv("LLM_MODE", "mock").lower()
    content = await _call_mock_llm(request) if llm_mode == "mock" else await _call_ollama(request)

    # L1 저장
    await _cache.set(l1_key, content)

    # L2 저장 (임베딩 재사용)
    # 저장 시도 langdetect도 정규화 후 적용 — 원본 쿼리에 영어 기술 용어 포함 시 오인식 방지
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
    _log_jsonl({
        "request_id": request_id, "event": "llm_call",
        "llm_mode": llm_mode, "latency_ms": round(elapsed, 2),
        "model": request.model, "messages_count": len(request.messages),
    })

    return ChatResponse(
        id=request_id, content=content,
        cached=False, latency_ms=round(elapsed, 2),
    )


@app.get("/health")
async def health() -> dict:
    """헬스체크 엔드포인트 (Phase 2 확장)."""
    redis_ok = False
    if _redis_client is not None:
        try:
            await _redis_client.ping()
            redis_ok = True
        except Exception:
            pass

    return {
        "status": "ok" if redis_ok else "degraded",
        "redis": redis_ok,
        "llm_mode": os.getenv("LLM_MODE", "mock"),
        "semantic_cache": {
            "enabled": _vector_cache is not None,
            "model_loaded": _embedding_model is not None,
            "threshold": os.getenv("SEMANTIC_THRESHOLD", "0.85"),
        },
    }
