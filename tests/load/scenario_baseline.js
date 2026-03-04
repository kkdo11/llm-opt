/**
 * 시나리오 1: Baseline (정상 부하)
 *
 * 목적: 정상 운영 부하에서 캐시 히트율 및 레이턴시 기준선 측정
 *
 * 패턴:
 *   - 10 VU가 3분간 지속적으로 요청
 *   - 동일 질문 반복 포함 (L1 캐시 히트 유발)
 *   - 유사 질문 포함 (L2 Semantic Cache 히트 유발)
 *
 * 예상 결과:
 *   - L1 히트: ~30% (동일 질문 반복)
 *   - L2 히트: ~40% (유사 질문)
 *   - LLM 호출: ~30% (완전 신규 질문)
 *   - P95: < 500ms (대부분 캐시 히트)
 *
 * 실행:
 *   k6 run tests/load/scenario_baseline.js
 *   k6 run tests/load/scenario_baseline.js -e BASE_URL=http://k8s-node:30080
 */

import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";
import { BASE_URL, SUCCESS_THRESHOLDS, buildChatPayload, buildOllamaChatPayload, COMMON_HEADERS } from "./common.js";

// 커스텀 메트릭
const cacheHitRate = new Rate("cache_hit_rate");
const llmCallRate = new Rate("llm_call_rate");
const ollamaEndpointDuration = new Trend("ollama_endpoint_duration");

export const options = {
  scenarios: {
    baseline_openai: {
      executor: "constant-vus",
      vus: 5,
      duration: "3m",
      exec: "runOpenAI",
    },
    baseline_ollama: {
      executor: "constant-vus",
      vus: 5,
      duration: "3m",
      exec: "runOllama",
    },
  },
  thresholds: {
    ...SUCCESS_THRESHOLDS,
    ollama_endpoint_duration: ["p(95)<500"],
  },
};

// OpenAI 호환 엔드포인트 테스트
export function runOpenAI() {
  const res = http.post(
    `${BASE_URL}/v1/chat/completions`,
    buildChatPayload(false),
    { headers: COMMON_HEADERS }
  );

  const ok = check(res, {
    "status 200": (r) => r.status === 200,
    "has content": (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.content !== undefined && body.content.length > 0;
      } catch {
        return false;
      }
    },
  });

  if (res.status === 200) {
    try {
      const body = JSON.parse(res.body);
      cacheHitRate.add(body.cached === true);
      llmCallRate.add(body.cached === false);
    } catch { /* ignore */ }
  }

  sleep(Math.random() * 2 + 1);  // 1-3초 대기 (현실적인 사용자 패턴)
}

// Ollama 호환 엔드포인트 테스트 (MindGraph 클라이언트 패턴)
export function runOllama() {
  const start = Date.now();
  const res = http.post(
    `${BASE_URL}/api/chat`,
    buildOllamaChatPayload(),
    { headers: COMMON_HEADERS }
  );
  ollamaEndpointDuration.add(Date.now() - start);

  check(res, {
    "status 200": (r) => r.status === 200,
    "ollama format: done=true": (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.done === true;
      } catch {
        return false;
      }
    },
    "ollama format: message.role=assistant": (r) => {
      try {
        const body = JSON.parse(r.body);
        return body.message && body.message.role === "assistant";
      } catch {
        return false;
      }
    },
  });

  sleep(Math.random() * 2 + 1);
}
