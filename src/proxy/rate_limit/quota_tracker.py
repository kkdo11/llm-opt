"""사용자별 월간 토큰 할당량 추적기.

Redis key: llm:quota:{user_id}:{YYYYMM} → 누적 토큰 수
월말 자동 만료 (EXPIREAT)로 다음 달 자동 초기화.

설계 결정:
  - 캐시 히트는 토큰 차감 없음: 실제 LLM 비용이 발생하지 않으므로
    할당량 = GPU/API 실비용 기준.
  - INCRBY + EXPIREAT 원자성: Redis 단일 커맨드로 레이스 컨디션 방지.
    (단, INCRBY와 EXPIREAT는 별도 호출 — 원자성 필요 시 Lua 스크립트 검토)
  - 기본 할당량: USER_QUOTA_TOKENS=100000 환경변수로 재설정 가능.
"""

import calendar
import os
from datetime import datetime
from enum import Enum

from redis.asyncio import Redis


class QuotaStatus(str, Enum):
    """할당량 사용 상태."""

    OK = "OK"           # < 80%
    WARNING = "WARNING"  # 80% ~ 99%
    EXCEEDED = "EXCEEDED"  # >= 100%


DEFAULT_QUOTA: int = int(os.getenv("USER_QUOTA_TOKENS", "100000"))


def _quota_key(user_id: str, dt: datetime | None = None) -> str:
    """월별 할당량 Redis 키를 생성한다.

    Args:
        user_id: 사용자 ID
        dt: 기준 날짜 (None이면 현재 시각)

    Returns:
        Redis 키 문자열 (예: llm:quota:user1:202602)
    """
    if dt is None:
        dt = datetime.utcnow()
    return f"llm:quota:{user_id}:{dt.strftime('%Y%m')}"


def _month_end_timestamp(dt: datetime | None = None) -> int:
    """현재 달의 마지막 날 23:59:59 UTC Unix timestamp를 반환한다.

    Args:
        dt: 기준 날짜 (None이면 현재 시각)

    Returns:
        Unix timestamp (정수)
    """
    if dt is None:
        dt = datetime.utcnow()
    last_day = calendar.monthrange(dt.year, dt.month)[1]
    end_of_month = datetime(dt.year, dt.month, last_day, 23, 59, 59)
    return int(end_of_month.timestamp())


class QuotaTracker:
    """사용자별 월간 토큰 할당량을 Redis에서 추적한다.

    Attributes:
        redis: Redis 비동기 클라이언트
        quota: 월간 토큰 할당량 (기본: USER_QUOTA_TOKENS 환경변수)
    """

    def __init__(self, redis: Redis, quota: int = DEFAULT_QUOTA) -> None:
        self.redis = redis
        self.quota = quota

    async def increment(self, user_id: str, tokens: int) -> int:
        """사용자의 월간 토큰 사용량을 증가시킨다.

        INCRBY로 누적 후 EXPIREAT으로 월말 자동 만료 설정.
        캐시 히트는 이 함수를 호출하지 않음 (실비용 없음).

        Args:
            user_id: 사용자 ID
            tokens: 추가할 토큰 수

        Returns:
            증가 후 총 사용량
        """
        key = _quota_key(user_id)
        total = await self.redis.incrby(key, tokens)
        # 키가 처음 생성된 경우에만 만료 설정 (이미 설정된 경우 덮어쓰지 않도록 EXPIREAT 재설정)
        # 매 요청마다 EXPIREAT 호출은 비용 미미하므로 단순하게 항상 설정
        await self.redis.expireat(key, _month_end_timestamp())
        return int(total)

    async def get_usage(self, user_id: str) -> int:
        """사용자의 현재 월간 토큰 사용량을 반환한다.

        Args:
            user_id: 사용자 ID

        Returns:
            현재 사용량 (키 없으면 0)
        """
        key = _quota_key(user_id)
        value = await self.redis.get(key)
        return int(value) if value is not None else 0

    async def check(self, user_id: str) -> QuotaStatus:
        """사용자의 할당량 상태를 확인한다.

        사용률:
          < 80%  → OK
          80~99% → WARNING
          >= 100% → EXCEEDED

        Args:
            user_id: 사용자 ID

        Returns:
            QuotaStatus 열거값
        """
        usage = await self.get_usage(user_id)
        ratio = usage / self.quota

        if ratio >= 1.0:
            return QuotaStatus.EXCEEDED
        if ratio >= 0.8:
            return QuotaStatus.WARNING
        return QuotaStatus.OK

    async def get_remaining(self, user_id: str) -> int:
        """사용자의 잔여 토큰 할당량을 반환한다.

        Args:
            user_id: 사용자 ID

        Returns:
            잔여 할당량 (음수 불가, 0 이상)
        """
        usage = await self.get_usage(user_id)
        return max(0, self.quota - usage)
