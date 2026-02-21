"""Redis Vector Cache 구현 (Phase 2 L2 Semantic Cache).

Redis Stack HNSW Index를 사용한 코사인 유사도 기반 시맨틱 캐시.

설계:
  - 임베딩 차원: 384 (all-MiniLM-L6-v2 고정)
  - 유사도 메트릭: COSINE
  - 키: llm:vec:{uuid} (Redis JSON)
  - 인덱스: llm_vector_idx (HNSW)

COSINE 거리 주의:
  Redis Vector Search는 COSINE 거리(0=동일, 2=완전 반대)를 반환한다.
  유사도 = 1.0 - distance 로 변환 후 threshold와 비교한다.
"""

import json
import logging
import uuid

import numpy as np
from redis.asyncio import Redis
from redis.commands.search.field import TagField, TextField, VectorField
from redis.commands.search.indexDefinition import IndexDefinition, IndexType
from redis.commands.search.query import Query

logger = logging.getLogger(__name__)

VECTOR_INDEX_NAME = "llm_vector_idx"
VECTOR_KEY_PREFIX = "llm:vec:"
VECTOR_DIM = 384


class VectorCache:
    """Redis HNSW 기반 시맨틱 캐시.

    Attributes:
        client: 비동기 Redis 클라이언트 (Redis Stack 필수)
        ttl: 캐시 유효 시간 (초)
        threshold: 코사인 유사도 임계값 (기본 0.85)
    """

    def __init__(self, client: Redis, ttl: int = 86400, threshold: float = 0.85) -> None:
        self.client = client
        self.ttl = ttl
        self.threshold = threshold

    async def ensure_index(self) -> None:
        """HNSW 인덱스가 없으면 생성한다.

        멱등성 보장: 이미 존재하면 예외를 무시한다.
        lifespan에서 서버 시작 시 1회만 호출할 것.

        HNSW 파라미터 선택 근거:
          - M=16: 기본값, 정확도와 메모리의 균형
          - EF_CONSTRUCTION=200: 인덱스 품질 (높을수록 정확하나 구축 느림)
          - FLAT 대신 HNSW: 캐시 항목 증가 시 O(log n) 검색 성능 보장
        """
        try:
            await self.client.ft(VECTOR_INDEX_NAME).info()
            logger.info("HNSW 인덱스 이미 존재: %s", VECTOR_INDEX_NAME)
        except Exception:
            schema = (
                TextField("$.model", as_name="model"),
                TagField("$.lang", as_name="lang"),
                VectorField(
                    "$.embedding",
                    "HNSW",
                    {
                        "TYPE": "FLOAT32",
                        "DIM": VECTOR_DIM,
                        "DISTANCE_METRIC": "COSINE",
                        "M": 16,
                        "EF_CONSTRUCTION": 200,
                    },
                    as_name="embedding",
                ),
            )
            definition = IndexDefinition(
                prefix=[VECTOR_KEY_PREFIX],
                index_type=IndexType.JSON,
            )
            await self.client.ft(VECTOR_INDEX_NAME).create_index(
                schema, definition=definition
            )
            logger.info("HNSW 인덱스 생성 완료: %s (DIM=%d, COSINE)", VECTOR_INDEX_NAME, VECTOR_DIM)

    async def search(
        self, embedding: np.ndarray
    ) -> tuple[str, float, dict] | None:
        """코사인 유사도로 가장 유사한 캐시 항목을 검색한다.

        Args:
            embedding: 쿼리 임베딩 벡터 (shape: [384], dtype: float32)

        Returns:
            (cached_content, similarity, metadata) 튜플.
            threshold 미만이거나 결과 없으면 None.
            metadata: {'lang': str, 'keywords': list[str], 'model': str}
        """
        query_vec = embedding.astype(np.float32).tobytes()

        query = (
            Query("(*)=>[KNN 1 @embedding $vec AS score]")
            .sort_by("score")
            .return_fields("score", "$.content", "$.model", "$.lang", "$.keywords")
            .dialect(2)
        )

        try:
            results = await self.client.ft(VECTOR_INDEX_NAME).search(
                query, query_params={"vec": query_vec}
            )
        except Exception as e:
            logger.warning("Vector Search 실패: %s", e)
            return None

        if not results.docs:
            return None

        doc = results.docs[0]
        # COSINE 거리(0~2) → 유사도(0~1)
        similarity = 1.0 - float(doc.score)

        if similarity < self.threshold:
            logger.debug("유사도 %.4f < threshold %.2f → L2 미스", similarity, self.threshold)
            return None

        content = getattr(doc, "$.content", "") or ""
        keywords_raw = getattr(doc, "$.keywords", "[]") or "[]"
        metadata = {
            "lang": getattr(doc, "$.lang", "") or "",
            "model": getattr(doc, "$.model", "") or "",
            "keywords": json.loads(keywords_raw) if isinstance(keywords_raw, str) else keywords_raw,
        }

        logger.debug("L2 Vector Search 히트: 유사도=%.4f", similarity)
        return content, similarity, metadata

    async def store(
        self,
        embedding: np.ndarray,
        content: str,
        model: str,
        lang: str,
        keywords: list[str],
    ) -> str:
        """임베딩과 응답을 Redis에 저장한다.

        pipeline으로 JSON set + expire를 원자적으로 실행한다.

        Args:
            embedding: 임베딩 벡터 (shape: [384])
            content: LLM 응답 텍스트
            model: 요청 모델명 (예: 'qwen2.5:14b')
            lang: 감지된 언어 코드 (예: 'ko', 'en')
            keywords: extract_keywords()로 추출된 키워드 리스트

        Returns:
            저장된 Redis 키 (llm:vec:{uuid})
        """
        doc_id = str(uuid.uuid4())
        key = f"{VECTOR_KEY_PREFIX}{doc_id}"

        doc = {
            "content": content,
            "model": model,
            "lang": lang,
            "keywords": keywords,
            "embedding": embedding.astype(np.float32).tolist(),
        }

        pipe = self.client.pipeline(transaction=False)
        pipe.json().set(key, "$", doc)
        pipe.expire(key, self.ttl)
        await pipe.execute()

        logger.debug("L2 Vector Cache 저장: key=%s lang=%s keywords=%s", key, lang, keywords)
        return key
