"""쿼리 정규화: 임베딩 전 영어 기술 용어를 한글 표준 용어로 통일.

목적:
  "파이썬 list와 tuple 차이" ↔ "파이썬 리스트와 튜플 차이"처럼
  동일 의미지만 한글/영어 혼용으로 임베딩 유사도가 낮은 문제를 해결.

적용 시점:
  임베딩 계산 전 (저장 시, 검색 시 모두 적용)
  → 같은 정규화된 표현으로 임베딩되므로 유사도 향상.

한계 (실측 확인):
  - 문장 종결형 차이 ("이란" vs "이 무엇인지")는 해결 불가
  - 사전에 없는 신규 용어는 직접 추가 필요
  - 도메인 모호한 단어 ("set", "int")는 의도적으로 제외
"""

import re

# 영어 기술 용어 → 한글 표준 용어 사전
# 설계 원칙:
#   - 긴 용어를 짧은 용어보다 먼저 등록 (linked list > list, dictionary > dict)
#   - 모호한 단어는 제외: "set"(집합/설정), "int"(integer/interface 접두사)
TECH_TERM_MAP: dict[str, str] = {
    # 자료구조 (multi-word 먼저)
    "linked list": "연결 리스트",
    "binary tree": "이진 트리",
    "hash table": "해시 테이블",
    "priority queue": "우선순위 큐",
    # 자료구조 (single-word)
    "dictionary": "딕셔너리",
    "list": "리스트",
    "tuple": "튜플",
    "dict": "딕셔너리",
    "array": "배열",
    "stack": "스택",
    "queue": "큐",
    "heap": "힙",
    "tree": "트리",
    "graph": "그래프",
    "string": "문자열",
    "boolean": "불리언",
    # ML/AI
    "machine learning": "머신러닝",
    "deep learning": "딥러닝",
    "overfitting": "오버피팅",
    "underfitting": "언더피팅",
    "neural network": "신경망",
    "gradient descent": "경사 하강법",
    # 알고리즘
    "sorting": "정렬",
    "searching": "탐색",
    "recursion": "재귀",
    "dynamic programming": "동적 프로그래밍",
}

# 긴 용어 먼저 매칭되도록 길이 내림차순 정렬
_sorted_terms = sorted(TECH_TERM_MAP.keys(), key=len, reverse=True)

# re.ASCII: 한글 문자가 \w로 인식되지 않아 \b 단어 경계가 올바르게 동작
# 예: "list와" → "list"와 "와" 사이에 \b 경계 발생 (한글은 ASCII \w가 아님)
_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in _sorted_terms) + r")\b",
    re.IGNORECASE | re.ASCII,
)


def normalize_query(text: str) -> str:
    """임베딩 전 쿼리 정규화를 수행한다.

    영어 기술 용어를 한글 표준 용어로 변환하여
    한글/영어 혼용으로 인한 임베딩 유사도 저하를 방지한다.

    저장 시와 검색 시 모두 동일하게 적용해야 효과가 있다.

    Args:
        text: 원본 쿼리 텍스트

    Returns:
        정규화된 텍스트. 변환 대상 없으면 원본 그대로 반환.

    Examples:
        >>> normalize_query("파이썬 list와 tuple 차이가 뭐야")
        '파이썬 리스트와 튜플 차이가 뭐야'
        >>> normalize_query("Stack과 Queue 자료구조 차이가 뭐야?")
        '스택과 큐 자료구조 차이가 뭐야?'
        >>> normalize_query("오버피팅이 무엇인지 설명해줘")
        '오버피팅이 무엇인지 설명해줘'
    """
    return _PATTERN.sub(lambda m: TECH_TERM_MAP[m.group(0).lower()], text)
