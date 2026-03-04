/**
 * k6 부하 테스트 공통 설정 및 유틸리티
 *
 * 성공 기준 (Phase 4):
 *   - 에러율 < 1%
 *   - P95 응답 시간 < 2,000ms
 *   - P99 응답 시간 < 5,000ms
 */

export const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";

// 공통 임계값 (모든 시나리오에서 사용)
export const SUCCESS_THRESHOLDS = {
  http_req_failed: ["rate<0.01"],          // 에러율 < 1%
  http_req_duration: [
    "p(95)<2000",                          // P95 < 2초
    "p(99)<5000",                          // P99 < 5초
  ],
};

// 테스트 사용자 목록 (다양한 user_id로 할당량 분산)
const TEST_USERS = [
  "user_load_1", "user_load_2", "user_load_3",
  "user_load_4", "user_load_5",
];

// 테스트 질문 풀 — MindGraph RAG 요청 패턴 반영
const TEST_QUESTIONS = [
  "Docker란 무엇인가?",
  "Kubernetes와 Docker의 차이점은?",
  "FastAPI는 어떤 언어로 만들어졌나?",
  "Redis는 어떤 용도로 사용하나?",
  "LLM 캐싱의 장점은 무엇인가?",
  "Semantic Cache는 어떻게 동작하나?",
  "Spring Boot와 FastAPI의 차이점은?",
  "PostgreSQL과 MySQL의 차이점은?",
  "Neo4j는 어떤 데이터베이스인가?",
  "RAG(Retrieval Augmented Generation)란 무엇인가?",
];

/**
 * 랜덤 채팅 요청 페이로드 생성
 * @param {boolean} stream - 스트리밍 여부
 * @returns {string} JSON 문자열
 */
export function buildChatPayload(stream = false) {
  const question = TEST_QUESTIONS[Math.floor(Math.random() * TEST_QUESTIONS.length)];
  const userId = TEST_USERS[Math.floor(Math.random() * TEST_USERS.length)];

  return JSON.stringify({
    model: "qwen2.5:14b",
    messages: [{ role: "user", content: question }],
    stream: stream,
    user_id: userId,
  });
}

/**
 * Ollama 포맷 채팅 요청 페이로드 생성 (MindGraph 클라이언트 패턴)
 * @returns {string} JSON 문자열
 */
export function buildOllamaChatPayload() {
  const question = TEST_QUESTIONS[Math.floor(Math.random() * TEST_QUESTIONS.length)];

  return JSON.stringify({
    model: "qwen2.5:14b",
    messages: [{ role: "user", content: question }],
    stream: false,
  });
}

export const COMMON_HEADERS = {
  "Content-Type": "application/json",
};
