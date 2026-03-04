/**
 * 시나리오 2: Ramp Up (증가 부하)
 *
 * 목적: 부하가 점진적으로 증가할 때 HPA 스케일 업 동작 확인
 *
 * 패턴:
 *   0분→1분:  10 VU  (정상)
 *   1분→3분:  50 VU  (증가 — HPA 스케일 업 트리거 기대)
 *   3분→5분: 100 VU  (피크 — 다중 포드에서 처리)
 *   5분→6분:  10 VU  (감소 — HPA 스케일 다운 안정화 시작)
 *
 * 성공 기준:
 *   - HPA 스케일 업: 1분평균 > 20 → 30초 이내 포드 추가
 *   - 스케일 업 이후에도 에러율 < 1% 유지
 *   - P95 < 2,000ms (스케일 업 전환 시점 포함)
 *
 * 확인 방법:
 *   kubectl get hpa llm-opt-hpa -w
 *   kubectl get pods -l app=llm-opt -w
 *
 * 실행:
 *   k6 run tests/load/scenario_ramp.js
 */

import http from "k6/http";
import { check, sleep } from "k6";
import { Counter } from "k6/metrics";
import { BASE_URL, SUCCESS_THRESHOLDS, buildChatPayload, COMMON_HEADERS } from "./common.js";

const requestsPerStage = new Counter("requests_per_stage");

export const options = {
  stages: [
    { duration: "1m",  target: 10  },  // 정상 부하 워밍업
    { duration: "2m",  target: 50  },  // 점진적 증가
    { duration: "2m",  target: 100 },  // 피크 (HPA 스케일 업 검증)
    { duration: "1m",  target: 10  },  // 감소 (HPA 스케일 다운 시작)
  ],
  thresholds: {
    ...SUCCESS_THRESHOLDS,
  },
};

export default function () {
  const res = http.post(
    `${BASE_URL}/v1/chat/completions`,
    buildChatPayload(false),
    {
      headers: COMMON_HEADERS,
      timeout: "10s",
    }
  );

  check(res, {
    "status 200 or 429": (r) => r.status === 200 || r.status === 429,
    "not 5xx": (r) => r.status < 500,
  });

  requestsPerStage.add(1);
  sleep(Math.random() * 1 + 0.5);  // 0.5-1.5초 대기
}
