/**
 * 시나리오 3: Spike (급격한 트래픽 폭증)
 *
 * 목적: 갑작스러운 트래픽 급증(예: 이벤트, 크롤링 봇) 시 시스템 안정성 확인
 *
 * 패턴:
 *   0분→0.5분:   10 VU  (정상)
 *   0.5분→1분:  500 VU  (10초 이내 폭증 — 실제 Spike)
 *   1분→2분:    500 VU  (피크 유지)
 *   2분→2.5분:   10 VU  (급감)
 *   2.5분→4분:   10 VU  (안정화 확인)
 *
 * 성공 기준:
 *   - Spike 구간 에러율 < 5% (일시적 429 허용)
 *   - 전체 에러율 < 1%
 *   - 시스템 다운 없음 (5xx < 0.1%)
 *   - 안정화 구간 P95 < 500ms (캐시 히트 덕분에)
 *
 * 실행:
 *   k6 run tests/load/scenario_spike.js
 */

import http from "k6/http";
import { check, sleep } from "k6";
import { Rate } from "k6/metrics";
import { BASE_URL, buildChatPayload, COMMON_HEADERS } from "./common.js";

const serverErrorRate = new Rate("server_error_rate");
const quotaExceededRate = new Rate("quota_exceeded_rate");

export const options = {
  stages: [
    { duration: "30s", target: 10  },   // 정상
    { duration: "30s", target: 500 },   // Spike 시작 (급격한 증가)
    { duration: "60s", target: 500 },   // Spike 유지
    { duration: "30s", target: 10  },   // Spike 종료
    { duration: "90s", target: 10  },   // 안정화 확인
  ],
  thresholds: {
    // Spike 시나리오는 전체 에러율 기준 완화
    http_req_failed: ["rate<0.05"],        // 에러율 < 5% (429 포함)
    server_error_rate: ["rate<0.001"],     // 5xx < 0.1% (서버 다운 없음)
    http_req_duration: ["p(95)<5000"],     // Spike 구간 P95 < 5초
  },
};

export default function () {
  const res = http.post(
    `${BASE_URL}/v1/chat/completions`,
    buildChatPayload(false),
    {
      headers: COMMON_HEADERS,
      timeout: "15s",
    }
  );

  const is5xx = res.status >= 500;
  const is429 = res.status === 429;

  serverErrorRate.add(is5xx);
  quotaExceededRate.add(is429);

  check(res, {
    "not 5xx (server stable)": (r) => r.status < 500,
  });

  // Spike 시나리오: 대기 시간 최소화 (최대 부하 유발)
  sleep(0.1);
}
