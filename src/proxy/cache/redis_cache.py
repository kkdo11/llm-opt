"""Redis Hash Cache 구현 (Phase 1 L1 캐시).

키 전략: MD5(JSON(messages)) → Redis String
TTL: 기본 24시간 (환경변수 CACHE_TTL로 조정 가능)

Phase 2에서 redis-stack의 Vector Search를 추가할 예정.
현재는 단순 Hash(exact match) 캐시만 구현.
"""

import hashlib
import json
import logging

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


def cache_key(messages: list[dict]) -> str:
    """메시지 리스트로부터 MD5 캐시 키를 생성한다.

    Args:
        messages: ChatMessage dict 리스트 (role, content 포함)

    Returns:
        'llm:cache:{md5_hex}' 형태의 Redis 키
    """
    serialized = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    md5_hash = hashlib.md5(serialized.encode("utf-8")).hexdigest()
    return f"llm:cache:{md5_hash}"


class RedisCache:
    """Redis 기반 LLM 응답 캐시.

    Attributes:
        client: 비동기 Redis 클라이언트
        ttl: 캐시 유효 시간 (초), 기본 86400 (24시간)
    """

    def __init__(self, client: Redis, ttl: int = 86400) -> None:
        self.client = client
        self.ttl = ttl

    async def get(self, key: str) -> str | None:
        """캐시에서 값을 조회한다.

        Args:
            key: Redis 키

        Returns:
            캐시된 응답 문자열, 미스 시 None
        """
        value = await self.client.get(key)
        if value is not None:
            logger.debug("cache hit: %s", key)
            return value.decode("utf-8")
        logger.debug("cache miss: %s", key)
        return None

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        """캐시에 값을 저장한다.

        Args:
            key: Redis 키
            value: 저장할 문자열 (LLM 응답)
            ttl: 유효 시간(초), None이면 인스턴스 기본값 사용
        """
        effective_ttl = ttl if ttl is not None else self.ttl
        await self.client.set(key, value.encode("utf-8"), ex=effective_ttl)
        logger.debug("cache set: %s (ttl=%ds)", key, effective_ttl)
