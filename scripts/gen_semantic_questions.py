"""Phase 2 Threshold 실험용 의미 유사 질문 쌍 생성.

각 쌍은 original(캐시 워밍용)과 paraphrase(히트 테스트용)로 구성.
expected_hit=True: 의미 동일 → 캐시 히트가 정답
expected_hit=False: 의미 다름 → 캐시 미스가 정답 (False Positive 감지용)

실행:
    python3 scripts/gen_semantic_questions.py
    → scripts/results/semantic_questions.json 생성
"""

import json
from pathlib import Path

SEMANTIC_PAIRS = [
    # ── 올바른 Hit 쌍 (expected_hit=True) ─────────────────────────────────
    {
        "original": "파이썬으로 버블 정렬 구현해줘",
        "paraphrase": "파이썬 버블소트 코드 짜줘",
        "expected_hit": True,
        "category": "paraphrase",
    },
    {
        "original": "파이썬에서 리스트와 튜플의 차이점을 설명해줘",
        "paraphrase": "파이썬 list와 tuple 차이가 뭐야",
        "expected_hit": True,
        "category": "paraphrase",
    },
    {
        "original": "HTTP와 HTTPS의 차이점은?",
        "paraphrase": "HTTPS가 HTTP와 다른 점이 뭐야?",
        "expected_hit": True,
        "category": "paraphrase",
    },
    {
        "original": "딥러닝과 머신러닝의 차이점을 간단히 설명해줘",
        "paraphrase": "머신러닝이랑 딥러닝 차이가 뭔지 알려줘",
        "expected_hit": True,
        "category": "paraphrase",
    },
    {
        "original": "오버피팅이 무엇인지 설명해줘",
        "paraphrase": "오버피팅이란 무엇인가요?",
        "expected_hit": True,
        "category": "paraphrase",
    },
    {
        "original": "스택과 큐의 차이점을 설명해줘",
        "paraphrase": "Stack과 Queue 자료구조 차이가 뭐야?",
        "expected_hit": True,
        "category": "paraphrase",
    },
    # ── False Positive 유발 쌍 (expected_hit=False) ────────────────────────
    {
        "original": "파이썬으로 정렬 알고리즘 설명해줘",
        "paraphrase": "자바로 정렬 알고리즘 설명해줘",
        "expected_hit": False,
        "category": "lang_mismatch",
    },
    {
        "original": "2024년 한국 GDP 성장률은?",
        "paraphrase": "2023년 한국 GDP 성장률은?",
        "expected_hit": False,
        "category": "year_mismatch",
    },
    {
        "original": "Python 3.12 주요 신기능을 알려줘",
        "paraphrase": "Python 3.11 주요 신기능을 알려줘",
        "expected_hit": False,
        "category": "version_mismatch",
    },
    {
        "original": "TCP와 UDP의 차이점을 설명해줘",
        "paraphrase": "HTTP와 HTTPS의 차이점을 설명해줘",
        "expected_hit": False,
        "category": "different_topic",
    },
]


def main() -> None:
    output_path = Path("scripts/results/semantic_questions.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(SEMANTIC_PAIRS, f, ensure_ascii=False, indent=2)

    total = len(SEMANTIC_PAIRS)
    true_hits = sum(1 for p in SEMANTIC_PAIRS if p["expected_hit"])
    false_hits = total - true_hits

    print(f"생성 완료: {total}개 쌍")
    print(f"  올바른 Hit (expected_hit=True): {true_hits}개")
    print(f"  False Positive 테스트 (expected_hit=False): {false_hits}개")
    print(f"저장 위치: {output_path}")


if __name__ == "__main__":
    main()
