"""Validation Layer: Semantic Cache False Positive 방지.

3단계 검증 파이프라인 (단락 평가):
  1. 언어 일치: 쿼리와 캐시의 언어 코드 비교
  2. 숫자/연도 키워드: 연도·버전 불일치 → Miss (시간 의존 정보 보호)
  3. 기술 키워드: 프로그래밍 언어 불일치 → Miss (언어 혼동 방지)

설계 원칙:
  False Positive(잘못된 응답 반환) > False Negative(느린 정확한 응답)
  → 불확실하면 항상 Miss로 보수적 처리

예상 성능 (실측 전):
  - Validation 오버헤드: +5~10ms
  - False Positive: 12% → <5% (PROJECT_CONTEXT.md 예상치)
"""

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 프로그래밍 언어 키워드 (소문자 통일)
PROGRAMMING_LANGUAGES: frozenset[str] = frozenset({
    "python", "파이썬", "py",
    "java", "자바",
    "javascript", "js", "자바스크립트",
    "typescript", "ts",
    "c++", "cpp", "c#", "csharp",
    "go", "golang",
    "rust", "러스트",
    "kotlin", "코틀린",
    "swift", "스위프트",
    "ruby", "루비",
    "php",
    "sql",
})

# 네트워크 프로토콜 키워드 (소문자 통일)
# Threshold 실험에서 발견된 미커버 케이스: "TCP/UDP 차이"와 "HTTP/HTTPS 차이"가
# PROGRAMMING_LANGUAGES 필터를 통과해 FP 발생 → 별도 집합으로 관리
NETWORK_PROTOCOLS: frozenset[str] = frozenset({
    "tcp", "udp", "http", "https",
    "grpc", "websocket", "ws",
    "mqtt", "ftp", "smtp",
    "dns", "ssl", "tls",
})

# 연도: 1900~2099
# re.ASCII: 한국어 "년" 등이 \w로 인식되는 유니코드 모드 방지
# (?:19|20): 비캡처 그룹 → findall이 전체 매치 "2024" 반환 (캡처 그룹이면 "20"만 반환됨)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b", re.ASCII)
# 버전: v1.2.3 또는 1.2.3 형태
_VERSION_RE = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b", re.ASCII)


@dataclass
class ValidationResult:
    """Validation 결과.

    Attributes:
        passed: True이면 캐시 히트 허용, False이면 LLM 호출
        reason: 결과 사유 (로깅 및 디버깅용)
    """

    passed: bool
    reason: str = ""


class SemanticValidator:
    """시맨틱 캐시 Validation Layer.

    각 validate_* 메서드는 독립적으로 테스트 가능하게 설계.
    validate()는 세 검증을 단락 평가로 순차 실행.
    """

    def validate(
        self,
        query_text: str,
        query_lang: str,
        cached_metadata: dict,
        similarity: float,
    ) -> ValidationResult:
        """전체 Validation 파이프라인을 실행한다.

        Args:
            query_text: 현재 쿼리 텍스트
            query_lang: 감지된 쿼리 언어 코드 (예: 'ko', 'en')
            cached_metadata: VectorCache.search()가 반환한 메타데이터
                {'lang': str, 'keywords': list[str], 'model': str}
            similarity: 코사인 유사도 (이미 threshold 초과한 상태)

        Returns:
            ValidationResult(passed=True/False, reason=...)
        """
        # 1단계: 언어 일치
        result = self.validate_language(query_lang, cached_metadata.get("lang", ""))
        if not result.passed:
            logger.debug("Validation 실패 [언어]: %s (similarity=%.4f)", result.reason, similarity)
            return result

        # 2단계: 숫자/연도/버전
        result = self.validate_numeric_keywords(
            query_text, cached_metadata.get("keywords", [])
        )
        if not result.passed:
            logger.debug("Validation 실패 [숫자]: %s (similarity=%.4f)", result.reason, similarity)
            return result

        # 3단계: 기술 키워드
        result = self.validate_tech_keywords(
            query_text, cached_metadata.get("keywords", [])
        )
        if not result.passed:
            logger.debug("Validation 실패 [기술]: %s (similarity=%.4f)", result.reason, similarity)
            return result

        return ValidationResult(passed=True, reason="all_checks_passed")

    def validate_language(self, query_lang: str, cached_lang: str) -> ValidationResult:
        """언어 코드 일치 여부를 검사한다.

        Args:
            query_lang: 쿼리 언어 코드 (예: 'ko')
            cached_lang: 캐시된 항목의 언어 코드

        Returns:
            언어가 다르면 passed=False
        """
        if not query_lang or not cached_lang:
            # 언어 감지 실패 시 보수적으로 통과
            return ValidationResult(passed=True, reason="lang_unknown_skip")

        if query_lang != cached_lang:
            return ValidationResult(
                passed=False,
                reason=f"lang_mismatch: {query_lang} != {cached_lang}",
            )
        return ValidationResult(passed=True, reason="lang_match")

    def validate_numeric_keywords(
        self, query_text: str, cached_keywords: list[str]
    ) -> ValidationResult:
        """숫자/연도/버전 키워드의 일치 여부를 검사한다.

        Case 3 방지: "2024년 GDP" vs "2023년 GDP" → 연도 다름 → Miss

        Args:
            query_text: 현재 쿼리
            cached_keywords: 저장 시 extract_keywords()로 추출된 키워드

        Returns:
            숫자 키워드가 불일치하면 passed=False
        """
        query_numbers = (
            set(_YEAR_RE.findall(query_text)) | set(_VERSION_RE.findall(query_text))
        )

        if not query_numbers:
            return ValidationResult(passed=True, reason="no_numeric_in_query")

        cached_text = " ".join(cached_keywords)
        cached_numbers = (
            set(_YEAR_RE.findall(cached_text)) | set(_VERSION_RE.findall(cached_text))
        )

        if query_numbers != cached_numbers:
            return ValidationResult(
                passed=False,
                reason=f"numeric_mismatch: query={query_numbers}, cached={cached_numbers}",
            )
        return ValidationResult(passed=True, reason="numeric_match")

    def validate_tech_keywords(
        self, query_text: str, cached_keywords: list[str]
    ) -> ValidationResult:
        """프로그래밍 언어 및 네트워크 프로토콜 키워드 일치를 검사한다.

        Case 1 방지: "파이썬 정렬" vs "자바 정렬" → 프로그래밍 언어 다름 → Miss
        프로토콜 방지: "TCP/UDP 차이" vs "HTTP/HTTPS 차이" → 프로토콜 다름 → Miss

        Args:
            query_text: 현재 쿼리
            cached_keywords: 저장 시 extract_keywords()로 추출된 키워드

        Returns:
            기술 키워드가 불일치하면 passed=False
        """
        query_lower = query_text.lower()
        cached_text = " ".join(k.lower() for k in cached_keywords)

        query_tech = (
            {lang for lang in PROGRAMMING_LANGUAGES if lang in query_lower}
            | {proto for proto in NETWORK_PROTOCOLS if proto in query_lower}
        )

        if not query_tech:
            return ValidationResult(passed=True, reason="no_tech_keyword_in_query")

        cached_tech = (
            {lang for lang in PROGRAMMING_LANGUAGES if lang in cached_text}
            | {proto for proto in NETWORK_PROTOCOLS if proto in cached_text}
        )

        if query_tech != cached_tech:
            return ValidationResult(
                passed=False,
                reason=f"tech_keyword_mismatch: query={query_tech}, cached={cached_tech}",
            )
        return ValidationResult(passed=True, reason="tech_keyword_match")


def extract_keywords(text: str) -> list[str]:
    """텍스트에서 Validation에 사용할 키워드를 추출한다.

    저장 시 호출되어 VectorCache 메타데이터로 함께 저장됨.
    추출 대상: 연도, 버전, 프로그래밍 언어, 네트워크 프로토콜.

    Args:
        text: 원본 쿼리 텍스트

    Returns:
        중복 제거된 키워드 리스트
    """
    keywords: list[str] = []
    keywords.extend(_YEAR_RE.findall(text))
    keywords.extend(_VERSION_RE.findall(text))

    text_lower = text.lower()
    keywords.extend(lang for lang in PROGRAMMING_LANGUAGES if lang in text_lower)
    keywords.extend(proto for proto in NETWORK_PROTOCOLS if proto in text_lower)

    return list(set(keywords))
