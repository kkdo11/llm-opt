# LLM-OPT: Inference Traffic Optimization Platform

## 프로젝트 개요
Semantic Caching + 트래픽 제어를 통한 LLM 운영 비용 최적화 플랫폼.
MindGraph-AI 운영 중 발견한 GPU 메모리 비효율 문제에서 출발.

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
│   │   ├── cache/               # L1 + L2 Cache
│   │   ├── validation/          # Validation Layer
│   │   ├── cost/                # 비용 보호 + 토큰 예측
│   │   └── rate_limit/          # Rate Limiter
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
│   ├── skills/                  # Claude Code Skills
│   └── agents/                  # Claude Code Subagents
└── docker-compose.yml
```

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
- Phase별 브랜치: phase-1/baseline, phase-2/semantic-cache 등

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
- **Phase 3 완료**: 토큰 예측 + 비용 계산 + 할당량 추적 + SSE 스트리밍 (2026-02-22)
- **다음**: Phase 4 — Kubernetes 배포 + Custom Metrics + HPA + k6 부하 테스트
- **세션 컨텍스트**: .claude/SESSION_STATE.md 참조

## 자주 쓰는 명령어
```bash
# 개발 서버
uvicorn src.proxy.main:app --reload --port 8000

# 테스트
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
- Phase 1: FastAPI Proxy + Redis Hash Cache + Prometheus
- Phase 2: SentenceTransformer(paraphrase-multilingual-MiniLM-L12-v2) + Redis Vector Search(HNSW) + Validation Layer
- Phase 3: 출력 토큰 예측 + Streaming 모니터링 + 사용자 할당량
- Phase 4: Custom Metrics Exporter + HPA(이동평균) + k6 부하 테스트
- Phase 5: Grafana 대시보드 + 실시간 비용 계산
