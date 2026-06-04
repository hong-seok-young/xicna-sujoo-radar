"""발주가능성 스코어링 — 룰 기반(LLM 無). 0~100점 + S/A/B/C 등급.

영업2팀 요구(2026-06-02): 각 항목에 '발주 가능성' 점수를 매겨 우선순위로 노출.
4개 축 가중합 (최대 100):
  - 신호 출처 (0~40): 발주 확실성·선행성. DART 1차(발주처 직접 결정) 최고.
  - 투자 규모 (0~30): 클수록 큰 발주 기회. 미공시는 중간값.
  - 시설 적합성 (0~20): 자이씨앤에이 시공영역(반도체CR·GMP바이오·이차전지) 부합도.
  - 실현 강도 (0~10): 신규>정정, 자본대비 투자비율, 부지/면적 확정, 준공이면 0.

등급: S 80+ (즉시 영업) / A 60~79 (선제 접촉) / B 40~59 / C <40.
가중치는 영업팀 화면예시(한국콜마 95·네패스아크 90·포스코 85·코스맥스 76·PKC 73)의
상대 순위·등급에 맞춰 튜닝. 추후 영업팀 피드백으로 재조정 가능.
"""
from __future__ import annotations

# ── 신호 출처 base (0~40) — 발주 확실성/선행성 ──
SOURCE_BASE = {
    "dart1": 38,      # 발주처 본인 시설투자 '결정' — 시공사 미정, 영업 골든타임
    "news_high": 34,  # 강한 시공 뉴스 (착공/신축/증설 + 공장/플랜트)
    "mfds": 26,       # 식약처 GMP 적합판정 — 간접 신호 (증설 여력)
    "dart2": 24,      # 이미 시공사 결정된 공급계약 — 협력사/경쟁사 동향용
    "news_mid": 16,
    "news_low": 8,
}

# ── 시설 적합성 (0~20) — categorize.py 카테고리명 기준 ──
FACILITY_SCORE = {
    "CR": 20, "제약/바이오": 20, "이차전지": 20,   # 핵심 시공영역
    "데이터센터": 18,                               # 첨단 산업시설 (클린전원·냉각 시공)
    "일반생산": 12,
    "식품/음료": 8, "R&D": 8, "화장품": 8,
    "기타": 0,
}

GRADE_CUTS = ((80, "S"), (60, "A"), (40, "B"), (0, "C"))


def grade_for(score: int) -> str:
    for cut, g in GRADE_CUTS:
        if score >= cut:
            return g
    return "C"


def _scale_score(amount_won: int) -> int:
    """투자규모 점수 (0~30). 미공시(0)는 14(중간 — 규모 미상이 작다는 뜻은 아님)."""
    if amount_won is None or amount_won <= 0:
        return 14
    eok = amount_won / 1e8  # 원 → 억
    if eok < 450:
        return 10
    if eok < 700:
        return 16
    if eok < 1000:
        return 20
    if eok < 2000:
        return 24
    if eok < 5000:
        return 27
    return 30


def _facility_score(categories) -> int:
    if not categories:
        return 0
    return max((FACILITY_SCORE.get(c, 0) for c in categories), default=0)


def _realization_score(*, is_new: bool, equity_ratio, has_site: bool, is_done: bool) -> int:
    """실현 강도 (0~10). 준공/완공이면 0 (영업 골든타임 너머)."""
    if is_done:
        return 0
    s = 4 if is_new else 1
    if equity_ratio is not None:
        if equity_ratio >= 30:
            s += 3
        elif equity_ratio >= 10:
            s += 2
        else:
            s += 1
    else:
        s += 1
    if has_site:
        s += 3
    return min(s, 10)


def score_opportunity(*, source: str, amount_won: int = 0, categories=None,
                      is_new: bool = True, equity_ratio=None,
                      has_site: bool = False, is_done: bool = False) -> dict:
    """발주가능성 점수 산출.

    Args:
      source: 'dart1' | 'dart2' | 'news_high' | 'news_mid' | 'news_low' | 'mfds'
      amount_won: 투자/계약 금액 (원 단위). 0이면 미공시.
      categories: 카테고리명 리스트 (예: ['CR','이차전지']). 최고 적합도 채택.
      is_new: 신규(True) vs 정정(False).
      equity_ratio: 자본대비 투자비율 (%). None이면 미상.
      has_site: 부지/면적 확정 여부.
      is_done: 준공/완공(시공 완료) 여부 → 점수 0 처리.

    Returns: {'score': int, 'grade': str, 'breakdown': {...}}
    """
    base = SOURCE_BASE.get(source, 10)
    scale = _scale_score(amount_won)
    facility = _facility_score(categories or [])
    realization = _realization_score(
        is_new=is_new, equity_ratio=equity_ratio, has_site=has_site, is_done=is_done
    )
    score = max(0, min(100, base + scale + facility + realization))
    if is_done:
        # 준공/완공 = 시공 완료, 영업 골든타임 너머 → 강제 강등 (C 이하)
        score = min(score, 30)
    return {
        "score": score,
        "grade": grade_for(score),
        "breakdown": {"source": base, "scale": scale,
                      "facility": facility, "realization": realization},
    }
