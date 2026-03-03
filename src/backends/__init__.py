"""LLM 백엔드 추상화 레이어 (Phase A-1).

환경변수 LLM_BACKEND로 백엔드 전환:
  LLM_BACKEND=ollama  → OllamaBackend (기본, 로컬 Qwen 2.5 14B)
  LLM_BACKEND=openai  → OpenAIBackend (OpenAI API)
"""

from src.backends.base import LLMBackend, LLMResponse
from src.backends.ollama_backend import OllamaBackend
from src.backends.openai_backend import OpenAIBackend

__all__ = ["LLMBackend", "LLMResponse", "OllamaBackend", "OpenAIBackend"]
