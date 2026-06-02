"""발주가능성 스코어링 테스트 — 룰 기반 점수/등급/순위."""
from __future__ import annotations

from src.common.scoring import score_opportunity, grade_for

EOK = 10 ** 8  # 1억 원


def _s(**kw) -> int:
    return score_opportunity(**kw)["score"]


def test_grade_cuts():
    assert grade_for(95) == "S"
    assert grade_for(80) == "S"
    assert grade_for(79) == "A"
    assert grade_for(60) == "A"
    assert grade_for(59) == "B"
    assert grade_for(40) == "B"
    assert grade_for(39) == "C"


def test_example_relative_ranking():
    """영업팀 화면예시 상대 순위 재현 (한국콜마 ≥ 네패스아크 ≥ 포스코)."""
    kolmar = _s(source="dart1", amount_won=970 * EOK, categories=["제약/바이오", "CR"],
                is_new=True, equity_ratio=7.3)
    napes = _s(source="dart1", amount_won=667 * EOK, categories=["CR"],
               is_new=True, equity_ratio=39.6)
    posco = _s(source="news_high", amount_won=0, categories=["이차전지"],
               is_new=True, has_site=True)
    assert kolmar >= napes >= posco
    assert grade_for(kolmar) == "S" and grade_for(napes) == "S"
    assert all(x >= 60 for x in (kolmar, napes, posco))  # 모두 A급 이상


def test_dart1_outranks_dart2_same_inputs():
    """발주처 직접결정(1차)이 시공사 공급계약(2차)보다 높다."""
    d1 = _s(source="dart1", amount_won=600 * EOK, categories=["CR"])
    d2 = _s(source="dart2", amount_won=600 * EOK, categories=["CR"])
    assert d1 > d2


def test_done_demoted_to_C():
    """준공/완공은 영업 무관 → 강제 강등(C)."""
    live = _s(source="news_high", amount_won=1000 * EOK, categories=["CR"], is_new=True)
    done = _s(source="news_high", amount_won=1000 * EOK, categories=["CR"], is_new=True, is_done=True)
    assert grade_for(live) in ("S", "A")
    assert grade_for(done) == "C"


def test_irrelevant_facility_demoted():
    """시공영역 밖(기타) 카테고리는 시설점수 0 → 큰 폭 강등."""
    core = _s(source="dart1", amount_won=500 * EOK, categories=["CR"])
    etc = _s(source="dart1", amount_won=500 * EOK, categories=["기타"])
    assert core - etc >= 15


def test_new_beats_correction():
    """신규가 정정보다 높다."""
    new = _s(source="dart1", amount_won=500 * EOK, categories=["CR"], is_new=True)
    cor = _s(source="dart1", amount_won=500 * EOK, categories=["CR"], is_new=False)
    assert new > cor


def test_larger_amount_scores_higher():
    big = _s(source="dart1", amount_won=3000 * EOK, categories=["CR"])
    small = _s(source="dart1", amount_won=500 * EOK, categories=["CR"])
    assert big > small
