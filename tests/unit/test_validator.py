"""SemanticValidator 단위 테스트.

외부 의존성 없음 — 순수 파이썬 로직 검증.
PROJECT_CONTEXT.md의 3가지 케이스를 직접 검증한다.
"""

import pytest

from src.proxy.validation.validator import (
    SemanticValidator,
    ValidationResult,
    extract_keywords,
)


@pytest.fixture
def validator() -> SemanticValidator:
    return SemanticValidator()


# ---------------------------------------------------------------------------
# validate_language
# ---------------------------------------------------------------------------


class TestLanguageValidation:
    def test_same_language_passes(self, validator: SemanticValidator) -> None:
        """같은 언어 코드는 통과해야 한다."""
        assert validator.validate_language("ko", "ko").passed is True

    def test_different_language_fails(self, validator: SemanticValidator) -> None:
        """다른 언어 코드는 실패해야 한다."""
        result = validator.validate_language("ko", "en")
        assert result.passed is False
        assert "lang_mismatch" in result.reason

    def test_unknown_query_lang_passes(self, validator: SemanticValidator) -> None:
        """쿼리 언어 감지 실패 시 보수적으로 통과해야 한다."""
        assert validator.validate_language("", "ko").passed is True

    def test_unknown_cached_lang_passes(self, validator: SemanticValidator) -> None:
        """캐시 언어 미기록 시 보수적으로 통과해야 한다."""
        assert validator.validate_language("ko", "").passed is True

    def test_both_unknown_passes(self, validator: SemanticValidator) -> None:
        """둘 다 알 수 없으면 통과해야 한다."""
        assert validator.validate_language("", "").passed is True


# ---------------------------------------------------------------------------
# validate_numeric_keywords
# ---------------------------------------------------------------------------


class TestNumericValidation:
    def test_different_years_fail(self, validator: SemanticValidator) -> None:
        """연도가 다르면 실패해야 한다 (Case 3: 시간 의존 정보)."""
        result = validator.validate_numeric_keywords(
            "2024년 한국 GDP 성장률은?", ["2023", "GDP"]
        )
        assert result.passed is False
        assert "numeric_mismatch" in result.reason

    def test_same_year_passes(self, validator: SemanticValidator) -> None:
        """같은 연도는 통과해야 한다."""
        result = validator.validate_numeric_keywords(
            "2024년 한국 GDP 성장률은?", ["2024", "GDP"]
        )
        assert result.passed is True

    def test_no_year_in_query_passes(self, validator: SemanticValidator) -> None:
        """쿼리에 연도 없으면 캐시에 연도가 있어도 통과해야 한다."""
        result = validator.validate_numeric_keywords(
            "파이썬 리스트 정렬 방법", ["2024"]
        )
        assert result.passed is True

    def test_different_versions_fail(self, validator: SemanticValidator) -> None:
        """버전이 다르면 실패해야 한다."""
        result = validator.validate_numeric_keywords(
            "Python 3.12 주요 신기능은?", ["3.11", "python"]
        )
        assert result.passed is False

    def test_same_version_passes(self, validator: SemanticValidator) -> None:
        """같은 버전은 통과해야 한다."""
        result = validator.validate_numeric_keywords(
            "Python 3.12 신기능은?", ["3.12", "python"]
        )
        assert result.passed is True

    def test_no_numeric_at_all_passes(self, validator: SemanticValidator) -> None:
        """쿼리와 캐시 모두 숫자 없으면 통과해야 한다."""
        result = validator.validate_numeric_keywords(
            "오버피팅이 무엇인지 설명해줘", []
        )
        assert result.passed is True


# ---------------------------------------------------------------------------
# validate_tech_keywords
# ---------------------------------------------------------------------------


class TestTechKeywordValidation:
    def test_different_language_fails(self, validator: SemanticValidator) -> None:
        """프로그래밍 언어 다르면 실패해야 한다 (Case 1)."""
        result = validator.validate_tech_keywords(
            "파이썬으로 정렬 알고리즘 설명해줘", ["자바", "정렬"]
        )
        assert result.passed is False
        assert "tech_keyword_mismatch" in result.reason

    def test_same_language_passes(self, validator: SemanticValidator) -> None:
        """같은 프로그래밍 언어는 통과해야 한다 (Case 2)."""
        result = validator.validate_tech_keywords(
            "파이썬 버블소트 코드 짜줘", ["파이썬", "버블정렬"]
        )
        assert result.passed is True

    def test_no_tech_keyword_passes(self, validator: SemanticValidator) -> None:
        """쿼리에 기술 키워드 없으면 통과해야 한다."""
        result = validator.validate_tech_keywords(
            "오버피팅이 무엇인지 설명해줘", []
        )
        assert result.passed is True

    def test_english_python_fails_against_java(self, validator: SemanticValidator) -> None:
        """영어 키워드도 인식해야 한다."""
        result = validator.validate_tech_keywords(
            "python sorting algorithm", ["java", "sorting"]
        )
        assert result.passed is False


# ---------------------------------------------------------------------------
# extract_keywords
# ---------------------------------------------------------------------------


class TestExtractKeywords:
    def test_extracts_year(self) -> None:
        keywords = extract_keywords("2024년 GDP 성장률 알려줘")
        assert "2024" in keywords

    def test_extracts_programming_language(self) -> None:
        keywords = extract_keywords("파이썬으로 버블 정렬 구현해줘")
        assert "파이썬" in keywords

    def test_extracts_version(self) -> None:
        keywords = extract_keywords("Python 3.12 신기능은?")
        assert "3.12" in keywords

    def test_no_duplicates(self) -> None:
        keywords = extract_keywords("파이썬 파이썬 파이썬")
        assert keywords.count("파이썬") == 1

    def test_empty_text(self) -> None:
        assert extract_keywords("") == []

    def test_multiple_types(self) -> None:
        keywords = extract_keywords("파이썬 3.11에서 2024년 신기능")
        assert "파이썬" in keywords
        assert "3.11" in keywords
        assert "2024" in keywords


# ---------------------------------------------------------------------------
# 전체 파이프라인 (PROJECT_CONTEXT.md 3가지 케이스)
# ---------------------------------------------------------------------------


class TestFullValidationPipeline:
    def test_case1_python_vs_java(self, validator: SemanticValidator) -> None:
        """Case 1: 파이썬 vs 자바 정렬 → similarity 0.92지만 False Positive 방지."""
        result = validator.validate(
            query_text="파이썬으로 정렬 알고리즘 설명해줘",
            query_lang="ko",
            cached_metadata={"lang": "ko", "keywords": ["자바", "정렬"]},
            similarity=0.92,
        )
        assert result.passed is False

    def test_case2_bubble_sort_paraphrase(self, validator: SemanticValidator) -> None:
        """Case 2: 파이썬 버블소트 paraphrase → 올바른 Hit."""
        result = validator.validate(
            query_text="파이썬 버블소트 코드 짜줘",
            query_lang="ko",
            cached_metadata={"lang": "ko", "keywords": ["파이썬", "버블정렬"]},
            similarity=0.91,
        )
        assert result.passed is True

    def test_case3_year_mismatch(self, validator: SemanticValidator) -> None:
        """Case 3: 2024 vs 2023 GDP → similarity 0.95지만 연도 다름 → Miss."""
        result = validator.validate(
            query_text="2024년 한국 GDP 성장률",
            query_lang="ko",
            cached_metadata={"lang": "ko", "keywords": ["2023", "GDP"]},
            similarity=0.95,
        )
        assert result.passed is False

    def test_language_mismatch_blocks_before_numeric(
        self, validator: SemanticValidator
    ) -> None:
        """언어 검사가 숫자 검사보다 먼저 실행되어야 한다 (단락 평가)."""
        result = validator.validate(
            query_text="2024년 GDP 성장률",  # 연도 있음
            query_lang="ko",
            cached_metadata={"lang": "en", "keywords": ["2024"]},  # 언어 다름
            similarity=0.90,
        )
        assert result.passed is False
        assert "lang_mismatch" in result.reason
