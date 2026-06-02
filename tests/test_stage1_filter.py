"""Stage 1 룰 필터 테스트."""
from __future__ import annotations

from datetime import datetime

from src.common.schema import Article
from src.stage1_filter.filter import evaluate, _find_areas, apply_filter


def _make(title: str, content: str = "") -> Article:
    return Article(
        id="t",
        source="test.com",
        url="https://test.com/x",
        title=title,
        content=content,
        published_at=datetime(2026, 5, 19),
    )


def test_passes_action_plus_target():
    """착공 + 공장 → 통과."""
    a = _make("삼성바이오, 송도 5공장 착공")
    r = evaluate(a)
    assert r.passed is True
    assert "착공" in r.matched_actions
    assert "공장" in r.matched_targets


def test_passes_target_plus_money():
    """공장 + 금액 → 통과."""
    a = _make("새 공장 짓는다", "총 1,500억원 규모")
    r = evaluate(a)
    assert r.passed is True
    assert any("1,500억" in m or "1500억" in m for m in r.matched_money)


def test_passes_target_plus_area():
    """공장 + 면적 → 통과."""
    a = _make("바이오 플랜트 신설", "연면적 5만㎡ 규모")
    r = evaluate(a)
    assert r.passed is True


def test_fails_only_action():
    """action만 있고 target 없으면 실패."""
    a = _make("신규 임원 선정")
    r = evaluate(a)
    assert r.passed is False


def test_fails_apartment():
    """아파트 분양 → 제외."""
    a = _make("강남 아파트 분양 시작")
    r = evaluate(a)
    assert r.passed is False


def test_fails_sports():
    """스포츠 → 제외 (애초에 target도 없음)."""
    a = _make("롯데 자이언츠 5연승")
    r = evaluate(a)
    assert r.passed is False


def test_passes_gmp_keyword():
    """GMP 인증 + 공장 → 통과."""
    a = _make("셀트리온 송도 3공장 GMP 인증 획득", "연간 25만L 규모")
    r = evaluate(a)
    assert r.passed is True
    assert "GMP" in r.matched_targets


def test_passes_clean_room():
    """클린룸 + 증설 → 통과."""
    a = _make("SK하이닉스 청주 클린룸 증설 발주", "공사비 1,500억")
    r = evaluate(a)
    assert r.passed is True


def test_passes_battery():
    """기가팩토리 + 증설 → 통과."""
    a = _make("LG엔솔 폴란드 기가팩토리 증설", "2조원 투자")
    r = evaluate(a)
    assert r.passed is True


# ── 면적 추출: 한글 '만' 단위 혼합 표기 정규화 (2026-06-02 버그픽스) ──

def test_area_myriad_mixed():
    """'4만1764㎡' → '41,764㎡' (만 앞/뒤 따로 잡혀 24배 축소되던 버그)."""
    assert _find_areas("부지 4만1764㎡ 규모")[0] == "41,764㎡"


def test_area_myriad_variants():
    """만 단위 단독·소수·공백·콤마 변형."""
    assert _find_areas("12만㎡")[0] == "120,000㎡"
    assert _find_areas("1.5만평")[0] == "15,000평"
    assert _find_areas("연면적 3만3000㎡")[0] == "33,000㎡"
    assert _find_areas("약 4만 1,764㎡")[0] == "41,764㎡"


def test_area_plain_unchanged():
    """일반 표기는 그대로."""
    assert _find_areas("750㎡ 매장")[0] == "750㎡"
    assert _find_areas("12,000㎡ 클린룸")[0] == "12,000㎡"


def test_area_no_false_match():
    """단위 글자 '평'(평택/평당)·금액(만원)을 면적으로 오매칭하지 않음."""
    assert _find_areas("케이엔제이, 평택 브레인시티 공장") == []
    assert _find_areas("취득금액은 약 435억9000만원") == []
    assert _find_areas("서울 강남, 평당 1억") == []


def test_area_e2e_keienjei():
    """E2E: 케이엔제이 실기사 → stage1 패턴에 'area:41,764㎡' 저장."""
    a = _make(
        "케이엔제이, 평택 브레인시티 신규 공장부지 확보…생산능력 확대 추진",
        "취득 부지는 경기도 평택 브레인시티 산업단지 내 4만1764㎡ 규모로 "
        "취득금액은 약 435억9000만원이다.",
    )
    out = apply_filter(a)
    assert "area:41,764㎡" in out.stage1_matched_patterns
