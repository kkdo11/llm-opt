"""비용 계산기.

토큰 수 → 비용(USD) 변환. OpenAI GPT-3.5 기준 단가를 기본값으로 사용.
환경변수로 재설정 가능하여 다른 모델/제공자에도 적용 가능.

설계 결정:
  - OpenAI 단가 기본값: Qwen2.5 실제 단가 없음. GPT-3.5 기준으로 추정.
  - 캐시 히트 비용 = 0: 실제 LLM 비용이 발생하지 않으므로.
  - 환경변수 오버라이드: 운영 환경에서 실제 단가로 조정 가능.
"""

import os


# [예상치] OpenAI GPT-3.5 기준 단가 — Qwen2.5 실제 단가 측정 후 보정 예정
COST_PER_INPUT_1K: float = float(os.getenv("COST_PER_INPUT_1K", "0.0005"))
COST_PER_OUTPUT_1K: float = float(os.getenv("COST_PER_OUTPUT_1K", "0.0015"))


class CostCalculator:
    """토큰 수를 USD 비용으로 변환하는 계산기.

    Attributes:
        cost_per_input_1k: 입력 토큰 1000개당 USD
        cost_per_output_1k: 출력 토큰 1000개당 USD
    """

    def __init__(
        self,
        cost_per_input_1k: float = COST_PER_INPUT_1K,
        cost_per_output_1k: float = COST_PER_OUTPUT_1K,
    ) -> None:
        self.cost_per_input_1k = cost_per_input_1k
        self.cost_per_output_1k = cost_per_output_1k

    def compute(self, input_tokens: int, output_tokens: int) -> float:
        """입출력 토큰 수에서 총 비용(USD)을 계산한다.

        Args:
            input_tokens: 입력 토큰 수
            output_tokens: 출력 토큰 수

        Returns:
            총 비용 (USD, 소수점 8자리)
        """
        input_cost = (input_tokens / 1000) * self.cost_per_input_1k
        output_cost = (output_tokens / 1000) * self.cost_per_output_1k
        return round(input_cost + output_cost, 8)

    def compute_savings(self, input_tokens: int, output_tokens: int) -> float:
        """캐시 히트로 절감된 비용(USD)을 계산한다.

        캐시 히트 시 실제 LLM 비용이 발생하지 않으므로
        절감액 = 원래 요청 비용.

        Args:
            input_tokens: 입력 토큰 수 (추정)
            output_tokens: 출력 토큰 수 (추정)

        Returns:
            절감된 비용 (USD)
        """
        return self.compute(input_tokens, output_tokens)
