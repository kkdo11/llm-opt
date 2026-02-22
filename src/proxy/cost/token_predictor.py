"""출력 토큰 예측기.

Rule-based 질문 유형 분류로 예측 출력 토큰 상한을 반환한다.
ML 모델 없이 키워드 패턴으로 충분 (CLAUDE.md 원칙: 측정 가능한 단순 구현 우선).

설계 결정:
  - tiktoken 미사용: Qwen tokenizer ≠ tiktoken, ±30% 추정이 150% 임계에 충분
  - UTF-8 바이트 / 4 ≈ ±30% 오차 → 150% 임계값 기준으로 허용 가능
  - 예상치: 실제 LLM 응답 통계 없음, 경험적 추정값 (Phase 3 완료 후 실측 보정 예정)
"""

from enum import Enum


class QueryType(str, Enum):
    """질문 유형 분류."""

    VERY_LONG = "VERY_LONG"
    LONG = "LONG"
    MEDIUM = "MEDIUM"
    SHORT = "SHORT"


# 유형별 예측 출력 토큰 상한
# [예상치] 실제 LLM 응답 통계 미수집 — Phase 3 완료 후 실측 보정 예정
PREDICTED_MAX_TOKENS: dict[QueryType, int] = {
    QueryType.VERY_LONG: 1200,
    QueryType.LONG: 800,
    QueryType.MEDIUM: 400,
    QueryType.SHORT: 150,
}

# 유형별 트리거 키워드 (우선순위 순)
_VERY_LONG_KEYWORDS = frozenset([
    "단계별", "방법", "튜토리얼", "가이드", "step by step",
    "how to", "tutorial", "guide", "절차", "과정",
])

_LONG_KEYWORDS = frozenset([
    "구현해줘", "구현해", "코드", "작성해줘", "작성해", "만들어줘", "만들어",
    "implement", "code", "write", "짜줘", "짜", "개발해줘", "개발해",
    "함수", "클래스", "프로그램", "스크립트",
])

_MEDIUM_KEYWORDS = frozenset([
    "설명해줘", "설명해", "차이점", "차이", "비교", "알려줘", "알려",
    "explain", "difference", "compare", "what is", "뭐야", "뭔가요",
    "무엇", "어떻게", "왜", "언제", "어디",
])


def classify_query(text: str) -> QueryType:
    """질문 텍스트를 유형으로 분류한다.

    우선순위: VERY_LONG > LONG > MEDIUM > SHORT (기본값).

    Args:
        text: 사용자 질문 텍스트

    Returns:
        QueryType 열거값
    """
    lower = text.lower()

    if any(kw in lower for kw in _VERY_LONG_KEYWORDS):
        return QueryType.VERY_LONG

    if any(kw in lower for kw in _LONG_KEYWORDS):
        return QueryType.LONG

    if any(kw in lower for kw in _MEDIUM_KEYWORDS):
        return QueryType.MEDIUM

    return QueryType.SHORT


def predict_output_tokens(text: str) -> int:
    """질문 텍스트에서 예측 출력 토큰 상한을 반환한다.

    Args:
        text: 사용자 질문 텍스트

    Returns:
        예측 출력 토큰 상한 (정수)
    """
    query_type = classify_query(text)
    return PREDICTED_MAX_TOKENS[query_type]


def estimate_tokens(text: str) -> int:
    """텍스트의 토큰 수를 추정한다.

    UTF-8 바이트 / 4 ≈ ±30% 오차.
    tiktoken 미사용 이유: Qwen2.5는 별도 tokenizer,
    150% 임계값 기준으로 ±30% 오차는 허용 가능.

    Args:
        text: 토큰 수를 추정할 텍스트

    Returns:
        추정 토큰 수 (최소 1)
    """
    return max(1, len(text.encode("utf-8")) // 4)
