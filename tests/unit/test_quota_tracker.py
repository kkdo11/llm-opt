"""QuotaTracker 단위 테스트.

Redis 의존성은 AsyncMock으로 완전히 격리한다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.proxy.rate_limit.quota_tracker import (
    QuotaStatus,
    QuotaTracker,
    _month_end_timestamp,
    _quota_key,
)


@pytest.fixture
def mock_redis() -> AsyncMock:
    """AsyncMock Redis 클라이언트."""
    redis = AsyncMock()
    redis.incrby = AsyncMock(return_value=0)
    redis.expireat = AsyncMock(return_value=True)
    redis.get = AsyncMock(return_value=None)
    return redis


@pytest.fixture
def tracker(mock_redis: AsyncMock) -> QuotaTracker:
    """테스트용 QuotaTracker (quota=1000)."""
    return QuotaTracker(redis=mock_redis, quota=1000)


class TestQuotaKey:
    """_quota_key() 함수 테스트."""

    def test_format(self) -> None:
        from datetime import datetime
        dt = datetime(2026, 2, 22)
        key = _quota_key("user1", dt)
        assert key == "llm:quota:user1:202602"

    def test_different_users(self) -> None:
        from datetime import datetime
        dt = datetime(2026, 2, 22)
        assert _quota_key("alice", dt) != _quota_key("bob", dt)


class TestMonthEndTimestamp:
    """_month_end_timestamp() 함수 테스트."""

    def test_returns_integer(self) -> None:
        ts = _month_end_timestamp()
        assert isinstance(ts, int)

    def test_february_2026(self) -> None:
        from datetime import datetime
        dt = datetime(2026, 2, 1)
        ts = _month_end_timestamp(dt)
        end = datetime(2026, 2, 28, 23, 59, 59)
        assert ts == int(end.timestamp())


class TestQuotaTrackerIncrement:
    """QuotaTracker.increment() 테스트."""

    @pytest.mark.asyncio
    async def test_increment_calls_incrby(
        self, tracker: QuotaTracker, mock_redis: AsyncMock
    ) -> None:
        mock_redis.incrby.return_value = 500
        result = await tracker.increment("user1", 500)
        mock_redis.incrby.assert_called_once()
        assert result == 500

    @pytest.mark.asyncio
    async def test_increment_sets_expireat(
        self, tracker: QuotaTracker, mock_redis: AsyncMock
    ) -> None:
        mock_redis.incrby.return_value = 100
        await tracker.increment("user1", 100)
        mock_redis.expireat.assert_called_once()


class TestQuotaTrackerCheck:
    """QuotaTracker.check() 테스트."""

    @pytest.mark.asyncio
    async def test_ok_when_below_80_percent(
        self, tracker: QuotaTracker, mock_redis: AsyncMock
    ) -> None:
        mock_redis.get.return_value = b"700"  # 70%
        status = await tracker.check("user1")
        assert status == QuotaStatus.OK

    @pytest.mark.asyncio
    async def test_warning_when_80_percent(
        self, tracker: QuotaTracker, mock_redis: AsyncMock
    ) -> None:
        mock_redis.get.return_value = b"800"  # 80%
        status = await tracker.check("user1")
        assert status == QuotaStatus.WARNING

    @pytest.mark.asyncio
    async def test_warning_when_between_80_and_100(
        self, tracker: QuotaTracker, mock_redis: AsyncMock
    ) -> None:
        mock_redis.get.return_value = b"950"  # 95%
        status = await tracker.check("user1")
        assert status == QuotaStatus.WARNING

    @pytest.mark.asyncio
    async def test_exceeded_when_at_100_percent(
        self, tracker: QuotaTracker, mock_redis: AsyncMock
    ) -> None:
        mock_redis.get.return_value = b"1000"  # 100%
        status = await tracker.check("user1")
        assert status == QuotaStatus.EXCEEDED

    @pytest.mark.asyncio
    async def test_exceeded_when_over_100_percent(
        self, tracker: QuotaTracker, mock_redis: AsyncMock
    ) -> None:
        mock_redis.get.return_value = b"1500"  # 150%
        status = await tracker.check("user1")
        assert status == QuotaStatus.EXCEEDED

    @pytest.mark.asyncio
    async def test_ok_when_no_usage(
        self, tracker: QuotaTracker, mock_redis: AsyncMock
    ) -> None:
        mock_redis.get.return_value = None  # 키 없음
        status = await tracker.check("user1")
        assert status == QuotaStatus.OK


class TestQuotaTrackerGetRemaining:
    """QuotaTracker.get_remaining() 테스트."""

    @pytest.mark.asyncio
    async def test_remaining_calculation(
        self, tracker: QuotaTracker, mock_redis: AsyncMock
    ) -> None:
        mock_redis.get.return_value = b"300"
        remaining = await tracker.get_remaining("user1")
        assert remaining == 700  # 1000 - 300

    @pytest.mark.asyncio
    async def test_remaining_not_negative(
        self, tracker: QuotaTracker, mock_redis: AsyncMock
    ) -> None:
        mock_redis.get.return_value = b"1500"  # 초과
        remaining = await tracker.get_remaining("user1")
        assert remaining == 0
