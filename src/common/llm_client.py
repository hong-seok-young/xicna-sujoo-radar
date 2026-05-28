"""Anthropic API 클라이언트 래퍼.

Stage 2/3에서 사용. 재시도·타임아웃·에러 처리 일원화.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from anthropic import Anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

# Stage별 권장 모델
MODEL_HAIKU = "claude-haiku-4-5-20251001"   # Stage 2 분류용 (싸고 빠름)
MODEL_SONNET = "claude-sonnet-4-6"          # Stage 3 추출용

_client: Anthropic | None = None


def get_client() -> Anthropic:
    """싱글톤 Anthropic 클라이언트."""
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set. Check .env file.")
        _client = Anthropic(api_key=api_key)
    return _client


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def call_llm(
    model: str,
    system: str,
    user: str,
    max_tokens: int = 1024,
    timeout: float = 60.0,
) -> str:
    """LLM 호출. 재시도 3회 (exponential backoff).

    Returns:
        텍스트 응답 (content[0].text)
    """
    client = get_client()
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        timeout=timeout,
    )
    # content 블록을 텍스트로 합치기
    parts = []
    for block in resp.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts)


def call_llm_json(
    model: str,
    system: str,
    user: str,
    max_tokens: int = 1024,
) -> dict[str, Any]:
    """JSON 응답 강제 파싱. ```json 펜스 제거 후 json.loads."""
    raw = call_llm(model, system, user, max_tokens)
    cleaned = raw.strip()
    # 코드 펜스 제거
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("LLM JSON 파싱 실패: %s\n원본:\n%s", e, raw)
        raise
