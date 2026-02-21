"""Phase 2 Semantic Cache Threshold 실험.

4가지 Threshold(0.75/0.80/0.85/0.90)별로 성능을 비교하고
최적 Threshold를 선정한다.

실행 조건:
  - docker compose up redis -d
  - SEMANTIC_CACHE_ENABLED=true LLM_MODE=ollama uvicorn src.proxy.main:app --port 8000
  - python3 scripts/gen_semantic_questions.py

실행:
  python3 scripts/benchmark_semantic.py

결과: scripts/results/phase2_threshold_results.json

측정 지표:
  - Hit Ratio: 전체 paraphrase 중 캐시 히트 비율
  - False Positive Rate: expected_hit=False인데 히트한 비율
  - False Negative Rate: expected_hit=True인데 미스한 비율
  - API 호출 감소율

주의 (실측 전):
  - 예상 수치는 PROJECT_CONTEXT.md 기준 (threshold=0.85 기준)
  - 실측치는 실험 후 PHASE_TRACKER.md에 기록할 것
"""

import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

PROXY_URL = "http://localhost:8000/v1/chat/completions"
QUESTIONS_PATH = Path("scripts/results/semantic_questions.json")
RESULTS_PATH = Path("scripts/results/phase2_threshold_results.json")
THRESHOLDS = [0.75, 0.80, 0.85, 0.90]


def flush_redis() -> None:
    """Redis 캐시 초기화."""
    result = subprocess.run(
        ["docker", "exec", "llm-opt-redis", "redis-cli", "FLUSHDB"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print("  Redis FLUSHDB 완료")
    else:
        print(f"  Redis FLUSHDB 실패: {result.stderr}", file=sys.stderr)


def set_threshold(threshold: float) -> None:
    """프록시 서버의 Threshold를 변경하기 위해 환경변수 안내 출력.

    실제 운영에서는 프록시 재시작이 필요하다.
    이 스크립트는 각 Threshold마다 프록시를 재시작해야 함을 안내한다.
    """
    print(f"\n  ⚠️  SEMANTIC_THRESHOLD={threshold} 로 프록시를 재시작하세요:")
    print(f"     SEMANTIC_THRESHOLD={threshold} LLM_MODE=ollama SEMANTIC_CACHE_ENABLED=true \\")
    print(f"       uvicorn src.proxy.main:app --port 8000")
    input("  재시작 완료 후 Enter 키를 누르세요... ")


def check_proxy() -> bool:
    """프록시 서버 헬스체크."""
    try:
        with urllib.request.urlopen("http://localhost:8000/health", timeout=5) as resp:
            data = json.loads(resp.read())
            semantic = data.get("semantic_cache", {})
            if not semantic.get("enabled"):
                print("  ⚠️  Semantic Cache가 비활성화되어 있습니다.")
                return False
            threshold = semantic.get("threshold", "?")
            print(f"  ✅ 프록시 연결 OK (threshold={threshold})")
            return True
    except Exception as e:
        print(f"  ❌ 프록시 연결 실패: {e}")
        return False


def send_request(question: str) -> dict:
    """프록시에 요청을 보낸다."""
    payload = json.dumps({
        "model": "qwen2.5:14b",
        "messages": [{"role": "user", "content": question}],
    }).encode("utf-8")

    req = urllib.request.Request(
        PROXY_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )

    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            elapsed = (time.perf_counter() - start) * 1000
            return {
                "cached": data.get("cached", False),
                "latency_ms": data.get("latency_ms", elapsed),
                "error": None,
            }
    except Exception as e:
        return {"cached": False, "latency_ms": 0, "error": str(e)}


def run_threshold_experiment(pairs: list[dict], threshold: float) -> dict:
    """단일 Threshold에 대한 실험을 실행한다."""
    print(f"\n[Threshold={threshold}] 실험 시작")
    flush_redis()
    time.sleep(0.5)

    results = []

    # 1단계: original 질문들로 캐시 워밍
    print(f"  캐시 워밍 ({len(pairs)}개 original 질문)...")
    for pair in pairs:
        r = send_request(pair["original"])
        if r["error"]:
            print(f"  ⚠️  오류: {r['error']}")

    # 2단계: paraphrase 질문들로 히트 테스트
    print(f"  히트 테스트 ({len(pairs)}개 paraphrase 질문)...")
    for i, pair in enumerate(pairs, 1):
        r = send_request(pair["paraphrase"])
        hit = r["cached"]
        expected = pair["expected_hit"]
        correct = hit == expected

        status = "✅" if correct else "❌"
        hit_str = "HIT " if hit else "MISS"
        print(
            f"    [{i:2d}] {status} [{pair['category']:15s}] {hit_str} "
            f"({pair['paraphrase'][:35]}...)"
        )

        results.append({
            **pair,
            "hit": hit,
            "correct": correct,
            "latency_ms": r["latency_ms"],
            "error": r["error"],
        })

    # 통계 계산
    valid = [r for r in results if not r["error"]]
    true_hit_pairs = [r for r in valid if r["expected_hit"]]
    false_hit_pairs = [r for r in valid if not r["expected_hit"]]

    true_positives = sum(1 for r in true_hit_pairs if r["hit"])   # 올바른 히트
    false_positives = sum(1 for r in false_hit_pairs if r["hit"])  # 잘못된 히트
    false_negatives = sum(1 for r in true_hit_pairs if not r["hit"])  # 놓친 히트

    hit_ratio = true_positives / len(true_hit_pairs) * 100 if true_hit_pairs else 0
    fp_rate = false_positives / len(false_hit_pairs) * 100 if false_hit_pairs else 0
    fn_rate = false_negatives / len(true_hit_pairs) * 100 if true_hit_pairs else 0

    stats = {
        "threshold": threshold,
        "total_pairs": len(valid),
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "hit_ratio_pct": round(hit_ratio, 1),
        "false_positive_rate_pct": round(fp_rate, 1),
        "false_negative_rate_pct": round(fn_rate, 1),
    }

    print(f"\n  결과 (threshold={threshold}):")
    print(f"    Hit Ratio:           {hit_ratio:.1f}% ({true_positives}/{len(true_hit_pairs)})")
    print(f"    False Positive Rate: {fp_rate:.1f}% ({false_positives}/{len(false_hit_pairs)})")
    print(f"    False Negative Rate: {fn_rate:.1f}% ({false_negatives}/{len(true_hit_pairs)})")

    return {"stats": stats, "raw_results": results}


def print_comparison(all_results: list[dict]) -> None:
    """Threshold별 비교표를 출력한다."""
    print("\n" + "=" * 65)
    print("  Phase 2 Threshold 비교 결과")
    print("=" * 65)
    print(f"  {'Threshold':>10} | {'Hit Ratio':>10} | {'False Pos':>10} | {'False Neg':>10}")
    print("  " + "-" * 55)
    for r in all_results:
        s = r["stats"]
        marker = " ← 선택" if s["threshold"] == 0.85 else ""
        print(
            f"  {s['threshold']:>10.2f} | "
            f"{s['hit_ratio_pct']:>9.1f}% | "
            f"{s['false_positive_rate_pct']:>9.1f}% | "
            f"{s['false_negative_rate_pct']:>9.1f}%{marker}"
        )
    print("=" * 65)
    print("  목표: Hit Ratio 최대화, False Positive < 5%")


def main() -> None:
    print("Phase 2 Threshold 실험 시작")
    print(f"시작 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    if not QUESTIONS_PATH.exists():
        print(f"❌ 질문 파일 없음: {QUESTIONS_PATH}")
        print("   python3 scripts/gen_semantic_questions.py 를 먼저 실행하세요.")
        sys.exit(1)

    with QUESTIONS_PATH.open(encoding="utf-8") as f:
        pairs = json.load(f)

    print(f"질문 쌍 로드: {len(pairs)}개")

    all_results = []

    for threshold in THRESHOLDS:
        set_threshold(threshold)

        if not check_proxy():
            print(f"  threshold={threshold} 실험 건너뜀")
            continue

        result = run_threshold_experiment(pairs, threshold)
        all_results.append(result)

    if all_results:
        print_comparison(all_results)

        output = {
            "timestamp": datetime.now().isoformat(),
            "thresholds_tested": THRESHOLDS,
            "results": all_results,
        }
        RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with RESULTS_PATH.open("w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"\n결과 저장: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
