"""Stage 1 룰 필터 테스트."""
from __future__ import annotations

from datetime import datetime

from src.common.schema import Article
from src.stage1_filter.filter import evaluate


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
