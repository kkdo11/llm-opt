"""Phase 1 벤치마크 스크립트.

측정 항목:
- 캐시 히트율 (Hit Ratio)
- 캐시 미스 평균 레이턴시 (= LLM 실제 호출 시간, Baseline)
- 캐시 히트 평균 레이턴시
- API 호출 감소율

실행 순서:
    1. Redis + Prometheus: docker compose up redis prometheus -d
    2. Proxy 서버: uvicorn src.proxy.main:app --port 8000 (LLM_MODE=ollama)
    3. 질문 생성: python3 scripts/gen_questions.py
    4. 벤치마크: python3 scripts/benchmark.py

결과: scripts/results/phase1_results.json
"""

import json
import statistics
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path


PROXY_URL = "http://localhost:8000/v1/chat/completions"
QUESTIONS_PATH = Path("scripts/results/test_questions.json")
RESULTS_PATH = Path("scripts/results/phase1_results.json")


def flush_redis() -> None:
    """Redis 캐시를 초기화한다 (실험 재현성 보장)."""
    import subprocess
    result = subprocess.run(
        ["docker", "exec", "llm-opt-redis", "redis-cli", "FLUSHDB"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print("Redis FLUSHDB 완료")
    else:
        print(f"Redis FLUSHDB 실패: {result.stderr}", file=sys.stderr)


def send_request(question: str, model: str = "qwen2.5:14b") -> dict:
    """프록시 서버에 요청을 보내고 응답과 레이턴시를 반환한다.

    Args:
        question: 질문 문자열
        model: 사용할 모델명

    Returns:
        {'cached', 'latency_ms', 'content', 'error'} 딕셔너리
    """
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": question}],
    }).encode("utf-8")

    req = urllib.request.Request(
        PROXY_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            elapsed = (time.perf_counter() - start) * 1000
            return {
                "cached": data.get("cached", False),
                # proxy 자체 측정 latency_ms 사용 (더 정확)
                "latency_ms": data.get("latency_ms", elapsed),
                "content_len": len(data.get("content", "")),
                "error": None,
            }
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        return {
            "cached": False,
            "latency_ms": elapsed,
            "content_len": 0,
            "error": str(e),
        }


def check_proxy_health() -> bool:
    """프록시 서버 헬스체크."""
    try:
        with urllib.request.urlopen("http://localhost:8000/health", timeout=5) as resp:
            data = json.loads(resp.read())
            return data.get("status") in ("ok", "degraded")
    except Exception:
        return False


def run_benchmark(questions: list[dict]) -> list[dict]:
    """벤치마크를 실행하고 결과를 반환한다."""
    results = []
    total = len(questions)

    for i, q in enumerate(questions, 1):
        print(f"  [{i:3d}/{total}] {'🔄' if q['is_duplicate'] else '🆕'} {q['question'][:40]}...", end=" ", flush=True)

        result = send_request(q["question"])

        status = "HIT " if result["cached"] else "MISS"
        latency = result["latency_ms"]
        error_mark = " ⚠️" if result["error"] else ""
        print(f"→ {status} {latency:7.1f}ms{error_mark}")

        results.append({
            **q,
            **result,
        })

    return results


def compute_stats(results: list[dict]) -> dict:
    """결과 통계를 계산한다."""
    errors = [r for r in results if r["error"]]
    valid = [r for r in results if not r["error"]]

    hits = [r for r in valid if r["cached"]]
    misses = [r for r in valid if not r["cached"]]

    hit_ratio = len(hits) / len(valid) * 100 if valid else 0
    api_reduction = len(hits) / len(valid) * 100 if valid else 0

    miss_latencies = [r["latency_ms"] for r in misses]
    hit_latencies = [r["latency_ms"] for r in hits]

    def safe_stats(values: list[float]) -> dict:
        if not values:
            return {"mean": 0, "median": 0, "p95": 0, "min": 0, "max": 0}
        sorted_v = sorted(values)
        p95_idx = int(len(sorted_v) * 0.95)
        return {
            "mean": round(statistics.mean(values), 1),
            "median": round(statistics.median(values), 1),
            "p95": round(sorted_v[p95_idx], 1),
            "min": round(min(values), 1),
            "max": round(max(values), 1),
        }

    return {
        "total_requests": len(results),
        "valid_requests": len(valid),
        "error_count": len(errors),
        "cache_hits": len(hits),
        "cache_misses": len(misses),
        "hit_ratio_pct": round(hit_ratio, 1),
        "api_reduction_pct": round(api_reduction, 1),
        "miss_latency_ms": safe_stats(miss_latencies),
        "hit_latency_ms": safe_stats(hit_latencies),
    }


def print_report(stats: dict) -> None:
    """결과 리포트를 출력한다."""
    print("\n" + "=" * 60)
    print("  Phase 1 벤치마크 결과")
    print("=" * 60)
    print(f"  총 요청:        {stats['total_requests']}개")
    print(f"  캐시 히트:      {stats['cache_hits']}개")
    print(f"  캐시 미스:      {stats['cache_misses']}개 (LLM 실제 호출)")
    print(f"  에러:           {stats['error_count']}개")
    print()
    print(f"  ✅ 캐시 히트율:  {stats['hit_ratio_pct']}%")
    print(f"  ✅ API 호출 감소: {stats['api_reduction_pct']}%")
    print()
    miss = stats["miss_latency_ms"]
    hit = stats["hit_latency_ms"]
    print(f"  📊 LLM 응답 시간 (캐시 미스 = Baseline):")
    print(f"     평균:  {miss['mean']}ms  |  중앙값: {miss['median']}ms  |  P95: {miss['p95']}ms")
    print()
    print(f"  📊 캐시 응답 시간 (캐시 히트):")
    print(f"     평균:  {hit['mean']}ms  |  중앙값: {hit['median']}ms  |  P95: {hit['p95']}ms")
    if miss["mean"] > 0 and hit["mean"] > 0:
        speedup = miss["mean"] / hit["mean"]
        print(f"  ⚡ 캐시 히트 시 속도 향상: {speedup:.0f}x 빠름")
    print("=" * 60)


def main() -> None:
    print("Phase 1 벤치마크 시작")
    print(f"시작 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # 헬스체크
    if not check_proxy_health():
        print("❌ 프록시 서버가 실행되지 않고 있습니다.")
        print("   uvicorn src.proxy.main:app --port 8000 을 먼저 실행하세요.")
        sys.exit(1)
    print("✅ 프록시 서버 연결 확인\n")

    # 질문 파일 로드
    if not QUESTIONS_PATH.exists():
        print(f"❌ 질문 파일 없음: {QUESTIONS_PATH}")
        print("   python3 scripts/gen_questions.py 를 먼저 실행하세요.")
        sys.exit(1)

    with QUESTIONS_PATH.open(encoding="utf-8") as f:
        questions = json.load(f)

    print(f"테스트 질문 로드: {len(questions)}개")
    print(f"(고유 {sum(1 for q in questions if not q['is_duplicate'])}개 + "
          f"중복 {sum(1 for q in questions if q['is_duplicate'])}개)\n")

    # Redis 캐시 초기화 (재현성 보장)
    flush_redis()
    print()

    # 벤치마크 실행
    print("벤치마크 실행 중...")
    results = run_benchmark(questions)

    # 통계 계산 및 출력
    stats = compute_stats(results)
    print_report(stats)

    # 결과 저장
    output = {
        "timestamp": datetime.now().isoformat(),
        "stats": stats,
        "raw_results": results,
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS_PATH.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
