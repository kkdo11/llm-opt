"""TokenPredictor 단위 테스트."""

import pytest

from src.proxy.cost.token_predictor import (
    QueryType,
    classify_query,
    estimate_tokens,
    predict_output_tokens,
)


class TestClassifyQuery:
    """classify_query() 함수 테스트."""

    def test_very_long_단계별(self) -> None:
        assert classify_query("파이썬 설치 단계별 가이드") == QueryType.VERY_LONG

    def test_very_long_튜토리얼(self) -> None:
        assert classify_query("Django 튜토리얼 처음부터 끝까지") == QueryType.VERY_LONG

    def test_very_long_방법(self) -> None:
        assert classify_query("도커 설치 방법 알려줘") == QueryType.VERY_LONG

    def test_long_코드(self) -> None:
        assert classify_query("파이썬으로 버블 정렬 코드 짜줘") == QueryType.LONG

    def test_long_구현해줘(self) -> None:
        assert classify_query("링크드 리스트 구현해줘") == QueryType.LONG

    def test_long_만들어줘(self) -> None:
        assert classify_query("REST API 서버 만들어줘") == QueryType.LONG

    def test_medium_설명해줘(self) -> None:
        assert classify_query("오버피팅이 무엇인지 설명해줘") == QueryType.MEDIUM

    def test_medium_차이점(self) -> None:
        assert classify_query("list와 tuple 차이점이 뭐야") == QueryType.MEDIUM

    def test_medium_비교(self) -> None:
        assert classify_query("TCP와 UDP 비교해줘") == QueryType.MEDIUM

    def test_short_기타(self) -> None:
        assert classify_query("안녕하세요") == QueryType.SHORT

    def test_short_빈문자열(self) -> None:
        assert classify_query("") == QueryType.SHORT

    # VERY_LONG이 LONG보다 우선순위 높음
    def test_priority_very_long_over_long(self) -> None:
        # "단계별"(VERY_LONG) + "코드"(LONG) → VERY_LONG 우선
        assert classify_query("단계별 파이썬 코드 작성 가이드") == QueryType.VERY_LONG


class TestPredictOutputTokens:
    """predict_output_tokens() 함수 테스트."""

    def test_very_long_returns_1200(self) -> None:
        assert predict_output_tokens("파이썬 설치 단계별 가이드") == 1200

    def test_long_returns_800(self) -> None:
        assert predict_output_tokens("버블 정렬 코드 짜줘") == 800

    def test_medium_returns_400(self) -> None:
        assert predict_output_tokens("오버피팅 설명해줘") == 400

    def test_short_returns_150(self) -> None:
        assert predict_output_tokens("안녕") == 150


class TestEstimateTokens:
    """estimate_tokens() 함수 테스트."""

    def test_ascii_text(self) -> None:
        # "hello" = 5 bytes → 5 // 4 = 1
        result = estimate_tokens("hello")
        assert result >= 1

    def test_minimum_returns_one(self) -> None:
        # 빈 문자열도 최소 1 반환
        assert estimate_tokens("") == 1

    def test_korean_text(self) -> None:
        # 한국어는 UTF-8에서 3bytes/char
        # "안녕하세요" = 15 bytes → 15 // 4 = 3
        result = estimate_tokens("안녕하세요")
        assert result >= 1

    def test_longer_text_more_tokens(self) -> None:
        short = estimate_tokens("hello")
        long_text = estimate_tokens("hello " * 100)
        assert long_text > short
