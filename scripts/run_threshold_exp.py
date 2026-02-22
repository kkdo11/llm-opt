"""Phase 2 Threshold 재실험 — KNN k=3 + 프로토콜 Validation 적용 후.

이전 실험 문제:
  - KNN k=1: 잘못된 원본 매칭 (문장 구조 유사도 과대평가)
  - threshold 0.75~0.80 절벽
  - 프로토콜 키워드 미커버 (TCP/UDP vs HTTP/HTTPS FP)

이번 변경 사항:
  - KNN k=3: 상위 3개 후보 중 Validation 통과하는 첫 번째 선택
  - NETWORK_PROTOCOLS 집합 추가: tcp/udp/http/https 등 구분
  - 테스트 threshold: 0.75 / 0.78 / 0.80 (절벽 구간 세분화)

실행:
  python3 scripts/run_threshold_exp.py

사전 조건:
  docker compose up redis -d
  Ollama 실행 중 (qwen2.5:14b)
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

PROXY_URL = "http://localhost:8000/v1/chat/completions"
QUESTIONS_PATH = Path("scripts/results/semantic_questions.json")
RESULTS_PATH = Path("scripts/results/phase2_threshold_results_v2.json")

# 이번 실험: 절벽 구간(0.75~0.80) 세분화
THRESHOLDS = [0.75, 0.78, 0.80]

PROXY_ENV = {
    **os.environ,
    "LLM_MODE": "ollama",
    "SEMANTIC_CACHE_ENABLED": "true",
    "EMBEDDING_MODEL": "paraphrase-multilingual-MiniLM-L12-v2",
    "REDIS_URL": "redis://localhost:6379",
    "LOG_LEVEL": "warning",  # 실험 중 로그 최소화
}


def flush_redis() -> None:
    subprocess.run(
        ["docker", "exec", "llm-opt-redis", "redis-cli", "FLUSHDB"],
        capture_output=True,
    )
    print("    Redis FLUSHDB 완료")


def start_proxy(threshold: float) -> subprocess.Popen:
    """uvicorn 프록시를 백그라운드로 시작한다."""
    env = {**PROXY_ENV, "SEMANTIC_THRESHOLD": str(threshold)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.proxy.main:app", "--port", "8000"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc


def stop_proxy(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def wait_for_proxy(timeout: int = 30) -> bool:
    """프록시가 준비될 때까지 대기한다."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://localhost:8000/health", timeout=3) as r:
                data = json.loads(r.read())
                if data.get("semantic_cache", {}).get("model_loaded"):
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def send_request(question: str) -> dict:
    payload = json.dumps({
        "model": "qwen2.5:14b",
        "messages": [{"role": "user", "content": question}],
    }).encode("utf-8")
    req = urllib.request.Request(
        PROXY_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {"cached": data.get("cached", False), "tier": data.get("tier"), "error": None}
    except Exception as e:
        return {"cached": False, "tier": None, "error": str(e)}


def run_experiment(pairs: list[dict], threshold: float) -> dict:
    print(f"\n{'='*60}")
    print(f"  Threshold = {threshold}")
    print(f"{'='*60}")

    # 1. Redis 초기화 먼저 (프록시 시작 전)
    # 주의: ensure_index()는 lifespan에서 1회만 실행된다.
    # 프록시 시작 후 FLUSHDB하면 인덱스도 삭제되어 "no such index" 에러 발생.
    # → FLUSHDB를 프록시 시작 전에 실행해야 한다.
    flush_redis()

    # 2. 프록시 시작 (이때 ensure_index()가 새 인덱스를 생성)
    print(f"  프록시 시작 (threshold={threshold})...")
    proc = start_proxy(threshold)
    if not wait_for_proxy():
        print("  ❌ 프록시 시작 실패")
        stop_proxy(proc)
        return {}

    threshold_actual = "?"
    try:
        with urllib.request.urlopen("http://localhost:8000/health", timeout=3) as r:
            threshold_actual = json.loads(r.read()).get("semantic_cache", {}).get("threshold", "?")
    except Exception:
        pass
    print(f"  ✅ 프록시 준비 (threshold={threshold_actual})")

    # 3. 캐시 워밍
    print(f"  캐시 워밍 ({len(pairs)}개 original)...")
    for pair in pairs:
        r = send_request(pair["original"])
        if r["error"]:
            print(f"    ⚠️  워밍 오류: {r['error'][:60]}")

    # 3. paraphrase 히트 테스트
    print(f"  히트 테스트 ({len(pairs)}개 paraphrase)...")
    results = []
    for i, pair in enumerate(pairs, 1):
        r = send_request(pair["paraphrase"])
        hit = r["cached"]
        expected = pair["expected_hit"]
        correct = hit == expected

        icon = "✅" if correct else "❌"
        hit_str = f"HIT({r['tier'] or '-'})" if hit else "MISS"
        print(f"    [{i:2d}] {icon} [{pair['category']:16s}] {hit_str:18s} {pair['paraphrase'][:38]}")

        results.append({**pair, "hit": hit, "tier": r["tier"], "correct": correct, "error": r["error"]})

    # 4. 프록시 종료
    stop_proxy(proc)
    time.sleep(1)

    # 5. 통계
    valid = [r for r in results if not r["error"]]
    true_pairs  = [r for r in valid if r["expected_hit"]]
    false_pairs = [r for r in valid if not r["expected_hit"]]

    tp = sum(1 for r in true_pairs if r["hit"])
    fp = sum(1 for r in false_pairs if r["hit"])
    fn = sum(1 for r in true_pairs if not r["hit"])

    hit_ratio = tp / len(true_pairs) * 100 if true_pairs else 0
    fp_rate   = fp / len(false_pairs) * 100 if false_pairs else 0
    fn_rate   = fn / len(true_pairs) * 100 if true_pairs else 0

    goal_hit = hit_ratio >= 40
    goal_fp  = fp_rate < 5
    goal_ok  = "✅ 목표 달성" if (goal_hit and goal_fp) else ("⚠️  FP 기준 미달" if not goal_fp else "⚠️  Hit 기준 미달")

    print(f"\n  [결과 threshold={threshold}]")
    print(f"    Hit Ratio:           {hit_ratio:.1f}%  ({'≥40% ✅' if goal_hit else '<40% ❌'})")
    print(f"    False Positive Rate: {fp_rate:.1f}%   ({'<5% ✅' if goal_fp else '≥5% ❌'})")
    print(f"    False Negative Rate: {fn_rate:.1f}%")
    print(f"    → {goal_ok}")

    return {
        "stats": {
            "threshold": threshold,
            "hit_ratio_pct": round(hit_ratio, 1),
            "false_positive_rate_pct": round(fp_rate, 1),
            "false_negative_rate_pct": round(fn_rate, 1),
            "true_positives": tp, "false_positives": fp, "false_negatives": fn,
        },
        "raw_results": results,
    }


def print_summary(all_results: list[dict]) -> None:
    print(f"\n{'='*65}")
    print("  Phase 2 Threshold 재실험 결과 요약 (KNN k=3 + 프로토콜 Validation)")
    print(f"{'='*65}")
    print(f"  {'Threshold':>10} | {'Hit Ratio':>10} | {'False Pos':>10} | {'False Neg':>10} | 목표")
    print("  " + "-" * 60)
    for r in all_results:
        if not r:
            continue
        s = r["stats"]
        goal = "✅" if s["hit_ratio_pct"] >= 40 and s["false_positive_rate_pct"] < 5 else "❌"
        print(
            f"  {s['threshold']:>10.2f} | "
            f"{s['hit_ratio_pct']:>9.1f}% | "
            f"{s['false_positive_rate_pct']:>9.1f}% | "
            f"{s['false_negative_rate_pct']:>9.1f}% | {goal}"
        )
    print(f"{'='*65}")
    print("  목표: Hit Ratio ≥ 40%  AND  False Positive < 5%")


def main() -> None:
    print("Phase 2 Threshold 재실험 (KNN k=3 + NETWORK_PROTOCOLS)")
    print(f"시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"테스트 Threshold: {THRESHOLDS}\n")

    if not QUESTIONS_PATH.exists():
        print(f"❌ 질문 파일 없음: {QUESTIONS_PATH}")
        print("   python3 scripts/gen_semantic_questions.py 를 먼저 실행하세요.")
        sys.exit(1)

    with QUESTIONS_PATH.open(encoding="utf-8") as f:
        pairs = json.load(f)
    print(f"질문 쌍: {len(pairs)}개 ({sum(1 for p in pairs if p['expected_hit'])}개 hit, {sum(1 for p in pairs if not p['expected_hit'])}개 FP테스트)")

    # 기존 프록시 정리
    subprocess.run(["pkill", "-f", "uvicorn src.proxy.main"], capture_output=True)
    time.sleep(1)

    all_results = []
    for threshold in THRESHOLDS:
        result = run_experiment(pairs, threshold)
        all_results.append(result)

    print_summary(all_results)

    output = {
        "timestamp": datetime.now().isoformat(),
        "experiment": "Phase 2 재실험 — KNN k=3 + NETWORK_PROTOCOLS",
        "thresholds_tested": THRESHOLDS,
        "results": all_results,
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS_PATH.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
