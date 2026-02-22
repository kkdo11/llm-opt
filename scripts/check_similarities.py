"""임베딩 유사도 직접 측정 스크립트.

파라프레이즈 쌍의 코사인 유사도를 직접 계산하여:
1. 정규화 전/후 유사도 변화 확인
2. 원본끼리의 유사도 (워밍 중 흡수 여부 예측)
3. 각 threshold에서 히트 가능 여부 결정

Redis 없이 동작함 (임베딩만 계산).

실행:
  python3 scripts/check_similarities.py

결과 저장:
  scripts/results/similarity_analysis_{timestamp}.json
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sentence_transformers import SentenceTransformer

from src.proxy.cache.normalizer import normalize_query

MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
RESULTS_DIR = Path("scripts/results")

print(f"모델 로딩: {MODEL_NAME}")
model = SentenceTransformer(MODEL_NAME)
print("모델 로딩 완료\n")


def sim(text1: str, text2: str) -> float:
    """두 텍스트의 코사인 유사도를 계산한다 (정규화 없음)."""
    emb1 = model.encode(text1, normalize_embeddings=True)
    emb2 = model.encode(text2, normalize_embeddings=True)
    return float(emb1 @ emb2)


def sim_normalized(text1: str, text2: str) -> float:
    """두 텍스트를 정규화 후 코사인 유사도를 계산한다."""
    return sim(normalize_query(text1), normalize_query(text2))


# ── 데이터 정의 ────────────────────────────────────────────────────────────
PARAPHRASE_PAIRS = [
    ("파이썬으로 버블 정렬 구현해줘", "파이썬 버블소트 코드 짜줘"),
    ("파이썬에서 리스트와 튜플의 차이점을 설명해줘", "파이썬 list와 tuple 차이가 뭐야"),
    ("HTTP와 HTTPS의 차이점은?", "HTTPS가 HTTP와 다른 점이 뭐야?"),
    ("딥러닝과 머신러닝의 차이점을 간단히 설명해줘", "머신러닝이랑 딥러닝 차이가 뭔지 알려줘"),
    ("오버피팅이 무엇인지 설명해줘", "오버피팅이란 무엇인가요?"),
    ("스택과 큐의 차이점을 설명해줘", "Stack과 Queue 자료구조 차이가 뭐야?"),
]

ORIGINALS = [
    "파이썬으로 버블 정렬 구현해줘",
    "파이썬에서 리스트와 튜플의 차이점을 설명해줘",
    "HTTP와 HTTPS의 차이점은?",
    "딥러닝과 머신러닝의 차이점을 간단히 설명해줘",
    "오버피팅이 무엇인지 설명해줘",
    "스택과 큐의 차이점을 설명해줘",
    "파이썬으로 정렬 알고리즘 설명해줘",
    "2024년 한국 GDP 성장률은?",
    "Python 3.12 주요 신기능을 알려줘",
    "TCP와 UDP의 차이점을 설명해줘",
]

# ── 결과 수집용 dict ────────────────────────────────────────────────────────
results: dict = {
    "timestamp": datetime.now().isoformat(),
    "model": MODEL_NAME,
    "paraphrase_pairs": [],
    "absorption_pairs": [],
    "similarity_matrix": [],
}

# ── 1. 파라프레이즈 쌍 유사도 (정규화 전/후) ─────────────────────────────
print("=" * 70)
print("  [1] 파라프레이즈 쌍 유사도 (정규화 전 / 정규화 후)")
print("=" * 70)
print(f"  {'쌍':>2}  {'정규화 전':>8}  {'정규화 후':>8}  {'변화':>6}  {'0.75 통과':>8}  파라프레이즈")
print("  " + "-" * 66)

for i, (orig, para) in enumerate(PARAPHRASE_PAIRS, 1):
    s_before = sim(orig, para)
    s_after = sim_normalized(orig, para)
    diff = s_after - s_before
    passes_75 = s_after >= 0.75
    print(f"  {i:>2}  {s_before:>8.4f}  {s_after:>8.4f}  {diff:>+6.4f}"
          f"  {'✅ YES' if passes_75 else '❌ NO ':6}  {para[:30]}")
    results["paraphrase_pairs"].append({
        "index": i,
        "original": orig,
        "paraphrase": para,
        "sim_before_norm": round(s_before, 4),
        "sim_after_norm": round(s_after, 4),
        "sim_delta": round(diff, 4),
        "passes_threshold_075": passes_75,
    })

# ── 2. 원본끼리 유사도 (워밍 중 흡수 여부 예측) ──────────────────────────
print("\n" + "=" * 70)
print("  [2] 원본끼리 유사도 (워밍 중 흡수 여부 예측, threshold=0.75)")
print("  (나중 original이 먼저 저장된 entry를 히트하면 저장 안 됨)")
print("=" * 70)

for i in range(1, len(ORIGINALS)):
    for j in range(i):
        s = sim_normalized(ORIGINALS[i], ORIGINALS[j])
        if s >= 0.75:
            print(f"  ⚠️  [{i+1}] → [{j+1}] sim={s:.4f} ≥0.75 → 흡수됨!")
            print(f"       [{i+1}] '{ORIGINALS[i]}'")
            print(f"       [{j+1}] '{ORIGINALS[j]}'")
            results["absorption_pairs"].append({
                "absorbed_index": i + 1,
                "absorbed_by_index": j + 1,
                "absorbed": ORIGINALS[i],
                "absorbed_by": ORIGINALS[j],
                "similarity": round(s, 4),
            })

# ── 3. 전체 유사도 행렬 ────────────────────────────────────────────────────
print("\n  [원본 쌍 유사도 행렬 — 0.75 이상 하이라이트]")
header = "       " + "".join(f"  [{j+1:2d}]" for j in range(len(ORIGINALS)))
print(f"  {header}")

matrix: list[list[float]] = []
for i, orig_i in enumerate(ORIGINALS):
    row_data: list[float] = []
    row = f"  [{i+1:2d}] "
    for j, orig_j in enumerate(ORIGINALS):
        if i == j:
            row += "  ----"
            row_data.append(1.0)
        else:
            s = sim_normalized(orig_i, orig_j)
            row_data.append(round(s, 4))
            if s >= 0.75:
                row += f"  \033[91m{s:.2f}\033[0m"
            else:
                row += f"  {s:.2f}"
    print(row)
    matrix.append(row_data)

results["similarity_matrix"] = matrix
results["matrix_labels"] = [f"[{i+1}] {orig[:20]}" for i, orig in enumerate(ORIGINALS)]

# ── 4. 결과 저장 ───────────────────────────────────────────────────────────
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
out_path = RESULTS_DIR / f"similarity_analysis_{timestamp}.json"
with out_path.open("w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print(f"\n결과 저장: {out_path}")
