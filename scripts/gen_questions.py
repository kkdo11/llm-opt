"""테스트 질문 셋 생성 스크립트.

20개 고유 질문 × 2회 반복 = 40개 요청 (캐시 히트율 목표: ~50%)

실행:
    python3 scripts/gen_questions.py
    → scripts/results/test_questions.json 생성
"""

import json
import random
from pathlib import Path

# 고유 질문 20개 (짧은 응답 유도 → 실험 시간 단축)
UNIQUE_QUESTIONS = [
    # Python 기초
    "파이썬에서 리스트와 튜플의 차이점을 간단히 설명해줘.",
    "파이썬 딕셔너리에서 키가 없을 때 기본값을 반환하는 방법은?",
    "파이썬 제너레이터와 일반 함수의 차이점은?",
    "파이썬에서 *args와 **kwargs의 차이점을 설명해줘.",
    "파이썬 리스트 컴프리헨션 예시를 간단히 보여줘.",
    # 자료구조 / 알고리즘
    "스택과 큐의 차이점을 한 문장으로 설명해줘.",
    "시간 복잡도 O(n log n)인 정렬 알고리즘 이름을 알려줘.",
    "해시 테이블의 충돌 해결 방법 두 가지를 말해줘.",
    "이진 탐색 트리에서 탐색 시간 복잡도는?",
    "DFS와 BFS의 차이점을 간단히 설명해줘.",
    # 시스템 / 네트워크
    "HTTP와 HTTPS의 차이점은?",
    "TCP와 UDP의 가장 큰 차이점은?",
    "REST API에서 GET과 POST의 차이점은?",
    "캐시와 쿠키의 차이점을 간단히 설명해줘.",
    "DNS가 하는 역할을 한 문장으로 설명해줘.",
    # AI / ML
    "딥러닝과 머신러닝의 차이점을 간단히 설명해줘.",
    "오버피팅이 무엇인지 간단히 설명해줘.",
    "배치 정규화(Batch Normalization)의 역할은?",
    "트랜스포머 모델에서 어텐션(Attention)이란?",
    "임베딩(Embedding)이 무엇인지 한 문장으로 설명해줘.",
]


def generate_test_set(repeats: int = 2, seed: int = 42) -> list[dict]:
    """테스트 질문 셋을 생성한다.

    Args:
        repeats: 각 고유 질문의 반복 횟수 (기본 2회)
        seed: 랜덤 시드

    Returns:
        {'idx', 'question', 'is_duplicate', 'original_idx'} 딕셔너리 리스트
    """
    random.seed(seed)

    questions = []
    # 1차: 모든 고유 질문 추가
    for i, q in enumerate(UNIQUE_QUESTIONS):
        questions.append({
            "idx": i,
            "question": q,
            "is_duplicate": False,
            "original_idx": i,
        })

    # 2차 이후: 반복 질문 추가 (순서 셔플)
    for _ in range(repeats - 1):
        duplicates = [
            {
                "idx": len(UNIQUE_QUESTIONS) + j,
                "question": UNIQUE_QUESTIONS[j],
                "is_duplicate": True,
                "original_idx": j,
            }
            for j in range(len(UNIQUE_QUESTIONS))
        ]
        random.shuffle(duplicates)
        questions.extend(duplicates)

    return questions


def main() -> None:
    output_path = Path("scripts/results/test_questions.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    questions = generate_test_set(repeats=2)

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(questions, f, ensure_ascii=False, indent=2)

    total = len(questions)
    duplicates = sum(1 for q in questions if q["is_duplicate"])
    print(f"생성 완료: {total}개 요청 ({len(UNIQUE_QUESTIONS)}개 고유 + {duplicates}개 중복)")
    print(f"저장 위치: {output_path}")
    print(f"예상 캐시 히트율: {duplicates / total * 100:.1f}%")


if __name__ == "__main__":
    main()
