"""VectorCache 단위 테스트.

Redis Stack은 AsyncMock으로 처리 — 실제 연결 없이 인터페이스만 검증.
test_cache.py의 AsyncRedisMock 패턴을 참고.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from src.proxy.cache.vector_cache import VectorCache, VECTOR_DIM, VECTOR_KEY_PREFIX


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Redis Stack 비동기 클라이언트 Mock."""
    client = AsyncMock()

    # FT (RediSearch) 모듈 Mock
    ft_mock = AsyncMock()
    ft_mock.info = AsyncMock(side_effect=Exception("Index not found"))
    ft_mock.create_index = AsyncMock()
    ft_mock.search = AsyncMock()
    client.ft = MagicMock(return_value=ft_mock)

    # JSON 모듈 Mock
    json_mock = MagicMock()
    json_mock.set = MagicMock(return_value=AsyncMock())
    client.json = MagicMock(return_value=json_mock)

    # Pipeline Mock
    pipe_mock = AsyncMock()
    pipe_mock.json = MagicMock(return_value=json_mock)
    pipe_mock.expire = AsyncMock()
    pipe_mock.execute = AsyncMock(return_value=[True, True])
    client.pipeline = MagicMock(return_value=pipe_mock)

    return client


@pytest.fixture
def vector_cache(mock_redis: AsyncMock) -> VectorCache:
    return VectorCache(mock_redis, ttl=3600, threshold=0.85)


def make_embedding(value: float = 0.0) -> np.ndarray:
    """테스트용 임베딩 벡터 생성."""
    vec = np.full(VECTOR_DIM, value, dtype=np.float32)
    return vec


# ---------------------------------------------------------------------------
# ensure_index
# ---------------------------------------------------------------------------


class TestEnsureIndex:
    @pytest.mark.asyncio
    async def test_creates_index_when_missing(
        self, vector_cache: VectorCache, mock_redis: AsyncMock
    ) -> None:
        """인덱스 없을 때 create_index를 호출해야 한다."""
        await vector_cache.ensure_index()
        mock_redis.ft().create_index.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_when_index_exists(
        self, vector_cache: VectorCache, mock_redis: AsyncMock
    ) -> None:
        """인덱스 이미 있으면 create_index를 호출하지 않아야 한다."""
        mock_redis.ft().info = AsyncMock(return_value={"index_name": "llm_vector_idx"})
        await vector_cache.ensure_index()
        mock_redis.ft().create_index.assert_not_called()

    @pytest.mark.asyncio
    async def test_idempotent_multiple_calls(
        self, vector_cache: VectorCache, mock_redis: AsyncMock
    ) -> None:
        """여러 번 호출해도 인덱스는 한 번만 생성되어야 한다."""
        # 두 번째 호출부터는 info가 성공한다고 가정
        call_count = 0

        async def info_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("not found")
            return {"index_name": "llm_vector_idx"}

        mock_redis.ft().info = info_side_effect
        await vector_cache.ensure_index()
        await vector_cache.ensure_index()
        assert mock_redis.ft().create_index.call_count == 1


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


class TestSearch:
    def _make_doc(self, score: str, content: str = "응답", lang: str = "ko") -> MagicMock:
        """Vector Search 단일 Document Mock 생성."""
        doc = MagicMock()
        # vec_score: KNN 별칭 (score는 redis-py 기본 점수 속성과 충돌하여 사용 불가)
        setattr(doc, "vec_score", score)
        setattr(doc, "$.content", content)
        setattr(doc, "$.lang", lang)
        setattr(doc, "$.model", "qwen2.5:14b")
        setattr(doc, "$.keywords", json.dumps(["파이썬"]))
        return doc

    def _make_search_result(self, score: str, content: str = "응답", lang: str = "ko") -> MagicMock:
        """Vector Search 결과 Mock 생성 (단일 후보)."""
        result = MagicMock()
        result.docs = [self._make_doc(score, content, lang)]
        return result

    def _make_multi_search_result(self, docs: list[MagicMock]) -> MagicMock:
        """Vector Search 결과 Mock 생성 (복수 후보)."""
        result = MagicMock()
        result.docs = docs
        return result

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_results(
        self, vector_cache: VectorCache, mock_redis: AsyncMock
    ) -> None:
        """검색 결과 없으면 빈 리스트를 반환해야 한다."""
        result_mock = MagicMock()
        result_mock.docs = []
        mock_redis.ft().search = AsyncMock(return_value=result_mock)

        result = await vector_cache.search(make_embedding())
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_below_threshold(
        self, vector_cache: VectorCache, mock_redis: AsyncMock
    ) -> None:
        """모든 후보가 threshold 미만이면 빈 리스트를 반환해야 한다."""
        # distance=0.20 → similarity=0.80 < 0.85
        mock_redis.ft().search = AsyncMock(
            return_value=self._make_search_result(score="0.20")
        )
        result = await vector_cache.search(make_embedding())
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_list_above_threshold(
        self, vector_cache: VectorCache, mock_redis: AsyncMock
    ) -> None:
        """threshold 이상이면 (content, similarity, metadata) 리스트를 반환해야 한다."""
        # distance=0.05 → similarity=0.95 >= 0.85
        mock_redis.ft().search = AsyncMock(
            return_value=self._make_search_result(score="0.05", content="캐시된 응답")
        )
        result = await vector_cache.search(make_embedding())

        assert len(result) == 1
        content, similarity, metadata = result[0]
        assert content == "캐시된 응답"
        assert abs(similarity - 0.95) < 0.001
        assert "lang" in metadata
        assert "keywords" in metadata

    @pytest.mark.asyncio
    async def test_returns_empty_on_search_error(
        self, vector_cache: VectorCache, mock_redis: AsyncMock
    ) -> None:
        """Vector Search 실패 시 예외 대신 빈 리스트를 반환해야 한다."""
        mock_redis.ft().search = AsyncMock(side_effect=Exception("connection error"))
        result = await vector_cache.search(make_embedding())
        assert result == []

    @pytest.mark.asyncio
    async def test_threshold_boundary_exact(
        self, vector_cache: VectorCache, mock_redis: AsyncMock
    ) -> None:
        """threshold 정확히 같은 값(0.85)은 후보에 포함해야 한다."""
        # distance=0.15 → similarity=0.85
        mock_redis.ft().search = AsyncMock(
            return_value=self._make_search_result(score="0.15")
        )
        result = await vector_cache.search(make_embedding())
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_returns_multiple_candidates_above_threshold(
        self, vector_cache: VectorCache, mock_redis: AsyncMock
    ) -> None:
        """threshold 이상인 복수 후보를 모두 반환해야 한다 (KNN k=3)."""
        # doc1: sim=0.95 (threshold 이상), doc2: sim=0.87 (이상), doc3: sim=0.78 (미만)
        docs = [
            self._make_doc("0.05", "응답1"),  # sim=0.95
            self._make_doc("0.13", "응답2"),  # sim=0.87
            self._make_doc("0.22", "응답3"),  # sim=0.78 → 제외
        ]
        mock_redis.ft().search = AsyncMock(
            return_value=self._make_multi_search_result(docs)
        )
        result = await vector_cache.search(make_embedding())

        assert len(result) == 2
        assert result[0][0] == "응답1"  # 유사도 높은 순
        assert result[1][0] == "응답2"

    @pytest.mark.asyncio
    async def test_stops_at_first_below_threshold(
        self, vector_cache: VectorCache, mock_redis: AsyncMock
    ) -> None:
        """threshold 미만 후보 이후는 순회를 중단해야 한다 (vec_score 정렬 보장)."""
        # doc1: sim=0.78 (미만) → break → doc2는 확인하지 않음
        docs = [
            self._make_doc("0.22", "응답1"),  # sim=0.78 → 미만
            self._make_doc("0.05", "응답2"),  # sim=0.95 → 도달 안 함
        ]
        mock_redis.ft().search = AsyncMock(
            return_value=self._make_multi_search_result(docs)
        )
        result = await vector_cache.search(make_embedding())
        assert result == []


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------


class TestStore:
    @pytest.mark.asyncio
    async def test_returns_key_with_prefix(
        self, vector_cache: VectorCache, mock_redis: AsyncMock
    ) -> None:
        """store()는 llm:vec: 접두사를 가진 키를 반환해야 한다."""
        key = await vector_cache.store(
            embedding=make_embedding(),
            content="응답",
            model="qwen2.5:14b",
            lang="ko",
            keywords=["파이썬"],
        )
        assert key.startswith(VECTOR_KEY_PREFIX)

    @pytest.mark.asyncio
    async def test_calls_pipeline(
        self, vector_cache: VectorCache, mock_redis: AsyncMock
    ) -> None:
        """store()는 pipeline을 통해 저장해야 한다."""
        await vector_cache.store(
            embedding=make_embedding(),
            content="응답",
            model="qwen2.5:14b",
            lang="ko",
            keywords=[],
        )
        mock_redis.pipeline.assert_called_once()

    @pytest.mark.asyncio
    async def test_each_store_has_unique_key(
        self, vector_cache: VectorCache, mock_redis: AsyncMock
    ) -> None:
        """서로 다른 store() 호출은 고유한 키를 반환해야 한다."""
        key1 = await vector_cache.store(make_embedding(0.1), "응답1", "qwen2.5:14b", "ko", [])
        key2 = await vector_cache.store(make_embedding(0.2), "응답2", "qwen2.5:14b", "ko", [])
        assert key1 != key2
