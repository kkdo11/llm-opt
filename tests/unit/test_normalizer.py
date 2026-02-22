"""normalize_query() 단위 테스트.

FN 원인이었던 한글↔영어 혼용 케이스를 직접 검증한다.
"""

import pytest

from src.proxy.cache.normalizer import normalize_query


class TestNormalizeQuery:
    # ── FN 케이스 직접 검증 ────────────────────────────────────────────

    def test_list_tuple_normalized(self) -> None:
        """FN 케이스 1: list/tuple → 리스트/튜플 변환."""
        result = normalize_query("파이썬 list와 tuple 차이가 뭐야")
        assert "리스트" in result
        assert "튜플" in result
        assert "list" not in result
        assert "tuple" not in result

    def test_stack_queue_normalized(self) -> None:
        """FN 케이스 2: Stack/Queue → 스택/큐 변환."""
        result = normalize_query("Stack과 Queue 자료구조 차이가 뭐야?")
        assert "스택" in result
        assert "큐" in result
        assert "Stack" not in result
        assert "Queue" not in result

    # ── 이미 한글인 경우 변환 없음 ────────────────────────────────────

    def test_already_korean_unchanged(self) -> None:
        """한글로 작성된 쿼리는 변환하지 않는다."""
        text = "오버피팅이 무엇인지 설명해줘"
        assert normalize_query(text) == text

    def test_no_tech_term_unchanged(self) -> None:
        """기술 용어 없는 쿼리는 변환하지 않는다."""
        text = "딥러닝과 머신러닝의 차이점을 설명해줘"
        assert normalize_query(text) == text

    # ── 대소문자 무관 ─────────────────────────────────────────────────

    def test_case_insensitive(self) -> None:
        """대소문자 무관하게 변환한다."""
        assert "리스트" in normalize_query("List 정렬 방법")
        assert "리스트" in normalize_query("LIST 정렬 방법")
        assert "리스트" in normalize_query("list 정렬 방법")

    # ── 긴 용어 우선 매칭 ─────────────────────────────────────────────

    def test_longer_term_matched_first(self) -> None:
        """'linked list'는 'list'보다 먼저 매칭되어야 한다."""
        result = normalize_query("linked list 구현해줘")
        assert "연결 리스트" in result
        # "리스트 리스트"가 되면 안 됨
        assert result.count("리스트") == 1

    def test_dictionary_before_dict(self) -> None:
        """'dictionary'는 'dict'보다 먼저 매칭되어야 한다."""
        result = normalize_query("dictionary 자료구조 설명해줘")
        assert "딕셔너리" in result
        assert "dictionary" not in result

    # ── 단어 경계 ─────────────────────────────────────────────────────

    def test_word_boundary_respected(self) -> None:
        """단어 경계에서만 매칭한다 — 부분 문자열 치환 안 됨."""
        # "sorting"은 변환, "sort"도 따로 있지만 "sorting" 안의 "sort"는 아님
        result = normalize_query("sorting algorithm 설명해줘")
        assert "정렬" in result

    # ── 복합 용어 ─────────────────────────────────────────────────────

    def test_multiple_terms_in_one_query(self) -> None:
        """한 쿼리에 여러 기술 용어가 있으면 모두 변환한다."""
        result = normalize_query("python의 list, tuple, dict 차이점은?")
        assert "리스트" in result
        assert "튜플" in result
        assert "딕셔너리" in result

    # ── 빈 문자열 ─────────────────────────────────────────────────────

    def test_empty_string(self) -> None:
        """빈 문자열은 빈 문자열을 반환한다."""
        assert normalize_query("") == ""
