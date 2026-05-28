"""Stage 2: LLM 분류 (Haiku).

stage1_passed=True인 기사를 입력받아,
"진짜 수주 단서인가?" Y/N 판정 + 산업군 라벨링.

비용: Haiku 사용. 1건당 약 10원 수준.
"""
from __future__ import annotations

import logging

from ..common.config import industries
from ..common.llm_client import MODEL_HAIKU, call_llm_json
from ..common.schema import Article

logger = logging.getLogger(__name__)


SYSTEM_PROMPT_TEMPLATE = """\
당신은 한국 영업팀의 수주정보 분류 어시스턴트입니다.

영업팀은 "공장·시설·연구소의 신설·증설·발주·투자" 사건을 찾고 있습니다.
다음 산업군 중 어디에 해당하는지 분류합니다:

{industries_block}

판정 기준:
- "is_relevant": 본문이 실제 시설 투자/발주/착공/증설 등 영업 단서를 다루면 "Y",
                단순 임원 동향·주가·정치·연예·M&A 루머 등이면 "N"
- "industry": 위 코드 중 하나. 명확히 분류 불가하면 "GENERAL".
- "reason": 판정 근거 한 문장.

반드시 다음 JSON 형식으로만 응답:
{{"is_relevant": "Y" 또는 "N", "industry": "코드", "reason": "..."}}
"""


def _build_system_prompt() -> str:
    cfg = industries()
    lines = []
    for ind in cfg["industries"]:
        lines.append(f"- {ind['code']}: {ind['name']} — {ind['description']}")
    return SYSTEM_PROMPT_TEMPLATE.format(industries_block="\n".join(lines))


def classify(article: Article) -> Article:
    """기사 1건을 LLM으로 분류. article을 수정해서 반환."""
    user_msg = f"제목: {article.title}\n\n본문:\n{article.content[:2000]}"
    try:
        result = call_llm_json(
            model=MODEL_HAIKU,
            system=_build_system_prompt(),
            user=user_msg,
            max_tokens=256,
        )
        article.stage2_relevant = result.get("is_relevant")
        article.stage2_industry = result.get("industry")
        article.stage2_reason = result.get("reason")
    except Exception as e:
        logger.error("Stage 2 classify 실패 (id=%s): %s", article.id, e)
        article.stage2_relevant = None
    return article
