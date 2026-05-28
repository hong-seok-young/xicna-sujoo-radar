"""Stage 3: LLM 구조화 추출 (Sonnet).

stage2_relevant="Y" 기사를 입력받아, 영업팀 요구사항 10번에 해당하는
구조화 필드를 JSON으로 추출.

비용: Sonnet 사용. 1건당 약 200원 수준.
"""
from __future__ import annotations

import logging

from ..common.llm_client import MODEL_SONNET, call_llm_json
from ..common.schema import Article

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """\
당신은 한국 영업팀의 수주정보 구조화 어시스턴트입니다.
기사에서 다음 필드를 가능한 만큼 추출하세요.
본문에 명시되지 않은 필드는 반드시 null로 두세요. 추측하지 마세요.

추출 필드:
- project_name: 프로젝트명 (예: "송도 5공장", "M15X 증설")
- client_name: 발주처/투자 주체 (예: "삼성바이오로직스")
- client_business: 발주처의 주요 업종 (예: "바이오의약품 위탁생산(CDMO)")
- investment_type: 투자 행위 — "신설" / "증설" / "이전" / "매입" / "인수" / "리뉴얼" 중 1개 또는 null
- amount_billion_krw: 예상 규모 (단위 억원, 숫자). "2조"는 20000, "1,500억"은 1500.
- location: 사업 위치 (예: "인천 송도", "베트남 하남성")
- land_area_m2: 대지면적 (㎡, 숫자). "5만㎡" → 50000
- total_floor_area_m2: 연면적 (㎡, 숫자)
- building_scale: 규모 (예: "지하1층~지상5층")
- cm_company: CM사 (예: "삼성물산")
- designer: 설계사 (예: "한미글로벌")
- schedule: 사업일정 (예: "2026년 7월 착공, 2027년 완공")
- summary: 한 줄 요약 (40자 이내)

반드시 JSON 한 객체로만 응답. 코드펜스(```) 금지.
"""


def extract(article: Article) -> Article:
    """기사 1건에서 구조화 필드 추출."""
    user_msg = f"제목: {article.title}\n\n본문:\n{article.content[:4000]}"
    try:
        result = call_llm_json(
            model=MODEL_SONNET,
            system=SYSTEM_PROMPT,
            user=user_msg,
            max_tokens=1024,
        )
        article.stage3_extracted = result
    except Exception as e:
        logger.error("Stage 3 extract 실패 (id=%s): %s", article.id, e)
        article.stage3_extracted = None
    return article
