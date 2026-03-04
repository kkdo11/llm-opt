/**
 * 시나리오 4: Soak (장시간 지속 부하)
 *
 * 목적: 메모리 누수, 연결 풀 고갈, Redis 연결 오류 등
 *       장시간 운영 시 발생하는 안정성 문제 탐지
 *
 * 패턴:
 *   0분→5분:   부하 증가 (워밍업)
 *   5분→55분:  30 VU 지속 (정상 부하 유지, 50분)
 *   55분→60분: 부하 감소 (쿨다운)
 *
 * 성공 기준:
 *   - 전체 에러율 < 1%
 *   - P95 < 2,000ms (시간 경과에 따른 성능 저하 없음)
 *   - 60분간 응답 시간 트렌드가 일정 (메모리 누수 없음)
 *
 * 실행:
 *   k6 run tests/load/scenario_soak.js --duration=60m
 *
 * 주의: 실행 시간이 길어 CI 파이프라인보다 수동 검증에 적합.
 *       --duration 플래그로 단축 가능 (예: --duration=10m for quick check)
 */

import http from "k6/http";
import { check, sleep } from "k6";
import { Trend, Rate } from "k6/metrics";
import { BASE_URL, SUCCESS_THRESHOLDS, buildChatPayload, COMMON_HEADERS } from "./common.js";

// 시간대별 레이턴시 추적 (메모리 누수 감지)
const latencyTrend = new Trend("latency_over_time");
const errorRate = new Rate("error_rate_over_time");

export const options = {
  stages: [
    { duration: "5m",  target: 30 },   // 워밍업
    { duration: "50m", target: 30 },   // Soak (50분 지속)
    { duration: "5m",  target: 0  },   // 쿨다운
  ],
  thresholds: {
    ...SUCCESS_THRESHOLDS,
    latency_over_time: ["p(95)<2000"],
    error_rate_over_time: ["rate<0.01"],
  },
};

export default function () {
  const start = Date.now();

  const res = http.post(
    `${BASE_URL}/v1/chat/completions`,
    buildChatPayload(false),
    {
      headers: COMMON_HEADERS,
      timeout: "10s",
    }
  );

  const duration = Date.now() - start;
  latencyTrend.add(duration);
  errorRate.add(res.status >= 400);

  check(res, {
    "status 200 or 429": (r) => r.status === 200 || r.status === 429,
    "response time < 10s": (r) => r.timings.duration < 10000,
  });

  // Soak: 현실적인 사용 패턴 (2-4초 간격)
  sleep(Math.random() * 2 + 2);
}
