# LLM-OPT: Inference Traffic Optimization Platform

## 프로젝트 개요
Semantic Caching + 트래픽 제어를 통한 LLM 운영 비용 최적화 플랫폼.
MindGraph-AI 운영 중 발견한 GPU 메모리 비효율 문제에서 출발.
**현재 목표: MindGraph-AI와 통합하여 실 워크로드 기반 검증 완성.**

## 디렉토리 구조
```
llm-opt/
├── CLAUDE.md                    # 이 파일
├── docs/
│   ├── PROJECT_CONTEXT.md       # 전체 아키텍처 및 기술 스펙
│   └── PHASE_TRACKER.md         # Phase별 진행 상황 및 실측치
├── src/
│   ├── proxy/                   # FastAPI Proxy 서버
│   │   ├── main.py
│   │   ├── models.py
│   │   ├── cache/               # L1 + L2 Cache
│   │   ├── validation/          # Validation Layer
│   │   ├── cost/                # 비용 보호 + 토큰 예측
│   │   ├── rate_limit/          # Rate Limiter
│   │   └── routes/
│   │       └── ollama_compat.py    # ★ MindGraph 연결용 Ollama 호환 엔드포인트
│   ├── backends/                   # ★ 멀티 백엔드 추상화
│   │   ├── base.py                 # LLMBackend ABC
│   │   ├── ollama_backend.py       # Ollama (Qwen 2.5 14B, 기본)
│   │   └── openai_backend.py       # OpenAI (선택적)
│   ├── metrics/                 # Prometheus exporter
│   └── scaling/                 # Custom HPA + Predictive Scaling
├── k8s/                         # Kubernetes manifests
├── monitoring/                  # Grafana dashboard JSON
├── tests/
│   ├── unit/
│   ├── integration/
│   └── load/                    # k6 스크립트
├── scripts/                     # 유틸리티 스크립트
├── .claude/
│   ├── SESSION_STATE.md         # 세션 상태 (재시작 시 읽기)
│   ├── skills/                  # Claude Code Skills
│   └── agents/                  # Claude Code Subagents
└── docker-compose.yml
```

## 백엔드 추상화 설계 (Phase A-1)

### 환경변수로 백엔드 전환
```bash
LLM_BACKEND=ollama    # 기본값 (Qwen 2.5 14B, 로컬)
LLM_BACKEND=openai    # OpenAI API (선택적)
```

### 백엔드 ABC (base.py)
```python
class LLMBackend(ABC):
    @abstractmethod
    async def chat(self, messages: list[dict], **kwargs) -> LLMResponse:
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        ...
```

### 설계 원칙
- 캐시/검증/비용 레이어는 백엔드와 무관하게 동작
- 백엔드는 main.py에서 환경변수 보고 주입 (DI)
- OpenAI 백엔드는 tiktoken으로 토큰 계산 (Ollama보다 정확)
- IMPORTANT: Ollama가 기본. OpenAI는 "추가 옵션"이지 대체가 아님

### 면접 포인트
> "처음엔 로컬 Qwen으로 개발했고, 아키텍처를 추상화해서 OpenAI도 백엔드로
>  선택 가능하게 만들었습니다. 환경변수 하나로 전환됩니다."

## Ollama 호환 엔드포인트 (Phase A-2, MindGraph 연결)

MindGraph는 LangChain4j Ollama 클라이언트를 사용.
LLM-OPT가 Ollama처럼 보이도록 호환 엔드포인트 제공.

```
MindGraph (LangChain4j)
  → POST /api/chat (Ollama 포맷)
  → ollama_compat.py (포맷 변환)
  → 기존 캐시 파이프라인 (그대로)
  → ollama_backend.py → 실제 Ollama
  → 응답을 Ollama 포맷으로 반환
  → MindGraph
```

MindGraph 설정 변경 (한 줄):
```properties
langchain4j.ollama.chat-model.base-url=http://localhost:8000
```

IMPORTANT: LangChain4j의 OllamaChatModel은 OpenAI 포맷(`/v1/chat/completions`)이 아니라
Ollama 네이티브 포맷(`/api/chat`)을 사용한다. 따라서 포맷 변환 레이어가 필수.

## 코딩 규칙

### Python
- Python 3.11+, FastAPI + uvicorn
- Type hint 필수 (함수 시그니처, 반환값)
- docstring: 한국어, Google style
- 비동기 우선 (async/await), 동기가 필요한 경우 주석으로 이유 명시
- import 순서: stdlib → third-party → local, isort로 정렬

### 네이밍
- 변수/함수: snake_case
- 클래스: PascalCase
- 상수: UPPER_SNAKE_CASE
- 파일: snake_case.py

### 테스트
- pytest + pytest-asyncio
- 새 기능마다 최소 1개 테스트 작성
- 테스트 파일: tests/unit/test_{module}.py
- fixture 활용, 외부 의존성은 mock 처리

### Git
- Conventional commits: feat:, fix:, test:, docs:, refactor:, perf:
- 한국어 커밋 메시지 허용, 단 prefix는 영어
- 예: `feat: Semantic Cache Validation Layer 구현`
- 브랜치: integration/mindgraph, feat/multi-backend, phase-4/k8s 등

### Docker & K8s
- 멀티스테이지 빌드, slim 이미지 사용
- K8s manifest는 k8s/ 디렉토리에 리소스별 분리
- 환경변수는 ConfigMap/Secret으로 관리

## 의사결정 원칙
- IMPORTANT: 성능 수치는 "예상"과 "실측"을 반드시 구분해서 코드 주석과 문서에 기록
- Trade-off 분석 없이 기술 선택하지 않기
- 복잡한 최적화보다 측정 가능한 단순한 구현 우선
- 에러 발생 시 원인 분석부터 — 임시 해결책은 TODO 주석과 함께만 허용

## 현재 진행 상황
- docs/PHASE_TRACKER.md 참조
- **Phase 1 완료**: Redis Hash Cache, Hit 50%, API 호출 50% 감소 (실측)
- **Phase 2 완료**: Semantic Cache (HNSW) + Validation Layer, Hit 66.7%, FP 0% (실측)
- **Phase 3 완료**: 토큰 예측 + 비용 계산 + 할당량 추적 + SSE 스트리밍 (2026-02-22, **커밋 미완료**)
- **다음**: Phase A — MindGraph 연결 (멀티 백엔드 + Ollama 호환 엔드포인트)
- **세션 컨텍스트**: .claude/SESSION_STATE.md 참조

## 전체 작업 순서

### Phase A: MindGraph 연결 (최우선)
```
A-1. src/backends/ 멀티 백엔드 추상화 구현
     - base.py (LLMBackend ABC)
     - ollama_backend.py (기존 Ollama 로직 이관)
     - openai_backend.py (OpenAI 옵션 추가)
     - main.py에서 LLM_BACKEND 환경변수로 주입

A-2. src/proxy/routes/ollama_compat.py
     - POST /api/chat (Ollama chat 포맷)
     - POST /api/generate (Ollama generate 포맷)
     - 기존 캐시 파이프라인 재사용 (변경 없음)
     - tests/unit/test_ollama_compat.py 작성

A-3. MindGraph 연결 검증
     - MindGraph application.properties base-url → http://localhost:8000
     - 실제 질문으로 LLM-OPT 로그에서 캐시 히트/미스 확인
     - PHASE_TRACKER Phase 1, 3의 "실 Ollama 측정 미완" 항목 채우기
```

### Phase B: MindGraph Neo4j 하이브리드 (mindgraph-ai 측 작업)
```
B-1~B-4: mindgraph-ai/CLAUDE.md 참조
```

### Phase 4: K8s + Adaptive Scaling
```
- Custom Metrics Exporter: Queue 길이 이동평균 → Prometheus Gauge
- HPA: Scale Up 1분평균 > 20 (30초 내), Scale Down 5분평균 < 5 (5분 후)
- k6 부하 테스트: 정상/증가/피크/Spike/Soak 시나리오
- 성공 기준: 1,000 동시접속 에러율 < 1%, HPA 응답 < 60초, P95 < 2초
```

### Phase 5: Grafana 대시보드
```
- MindGraph 실 워크로드 기반 비용 시각화
- 패널: 실시간 비용 누적 / Cache Hit Ratio / 비용 비교 / 레이턴시 분포 / Pod Scaling
```

## 자주 쓰는 명령어
```bash
# 개발 서버 (Ollama 백엔드, 기본)
LLM_BACKEND=ollama uvicorn src.proxy.main:app --reload --port 8000

# 개발 서버 (OpenAI 백엔드)
LLM_BACKEND=openai OPENAI_API_KEY=sk-... uvicorn src.proxy.main:app --reload --port 8000

# 개발 서버 (기존 방식, mock 모드)
uvicorn src.proxy.main:app --reload --port 8000

# 테스트 (107개, ~5초)
pytest tests/ -v
pytest tests/unit/ -v --cov=src

# Redis
docker compose up redis -d
redis-cli ping

# k6 부하 테스트
k6 run tests/load/scenario_basic.js

# Docker
docker compose up --build
docker compose down
```

## Phase별 핵심 기술 요소
- Phase 1: FastAPI Proxy + Redis Hash Cache + Prometheus ✅
- Phase 2: SentenceTransformer + Redis Vector Search(HNSW) + Validation Layer ✅
- Phase 3: 출력 토큰 예측 + Streaming 모니터링 + 사용자 할당량 ✅ (실측 미완)
- Phase A: Ollama 호환 엔드포인트 + 멀티 백엔드 추상화 + MindGraph 연결
- Phase B: MindGraph Neo4j 하이브리드 (mindgraph-ai 측)
- Phase 4: Custom Metrics Exporter + HPA(이동평균) + k6 부하 테스트
- Phase 5: Grafana 대시보드 + 실시간 비용 계산 (MindGraph 실 워크로드 기반)
