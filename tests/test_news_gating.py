"""뉴스 HIGH 게이팅 테스트 — 시공신호 판정 / 공급사 게이트 (2026-06-12).

영업팀 검토: "유효한 리드는 살리고 노이즈만 강등" — 컨텍스트 기반 키워드 필터.
06-12 리포트 실데이터에서 뽑은 대표 케이스를 회귀 방지로 고정한다.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import daily_report_html as R  # noqa: E402


def _it(title: str, content: str = "", patterns=None) -> dict:
    return {"title": title, "content": content,
            "stage1_matched_patterns": patterns or []}


# ── 시공 신호 판정 ──────────────────────────────────────────────────────────

def test_signal_strong_own_build():
    """본인 '공장 증설' = 명백한 시공 신호 (앰코 광주 후공정 공장 증설)."""
    it = _it("앰코, 광주에 1조 투자 검토…반도체 후공정 공장 증설",
             "앰코가 광주사업장에 1조원 규모 투자를 검토 중이다.",
             ["action:증설,투자,검토", "target:공장,후공정"])
    assert R._news_construction_signal(it) is True


def test_signal_capacity_expansion():
    """'생산능력 확충' = 진짜 캐파 증설 (유한양행) — '확충' 비유와 구분."""
    it = _it("유한양행, API 수주 증가에 생산능력 확충",
             "유한양행이 원료의약품 생산능력 확충 계획을 내놨다.",
             ["action:확충", "target:원료의약품"])
    assert R._news_construction_signal(it) is True


def test_signal_metaphor_expansion_excluded():
    """'영토/사업 확장' 같은 비유는 시공 신호 아님 (K-메모리 영토 확장)."""
    it = _it("K-메모리 영토 확장…슈퍼컴·PC·로보틱스 수요 블랙홀",
             "메모리 수요가 폭발하며 영토를 확장하고 있다.",
             ["action:확장", "target:공장"])
    assert R._news_construction_signal(it) is False


def test_signal_ai_partnership_pr_excluded():
    """AI 협력·동맹 PR 은 건물 발주 아님 → 시공 신호 아님 (젠슨 황 동맹)."""
    it = _it("젠슨 황, '반도체'부터 '로보틱스'까지 韓산업계와 동맹",
             "엔비디아와 AI 팩토리, 차세대 메모리 공동개발에 협력한다.",
             ["action:투자,확대", "target:공장"])
    assert R._news_construction_signal(it) is False


def test_signal_negated_expansion_excluded():
    """'증설은 아니다' 부정문은 시공 신호 아님 (하이닉스 375단 낸드 양산)."""
    it = _it("하이닉스, 연말 375단 낸드 양산…몰리브덴 첫 도입",
             "신규 공장 증설은 아니다. 청주 기존 M15 공장 생산라인을 전환한다.",
             ["action:증설,양산", "target:공장"])
    assert R._news_construction_signal(it) is False


def test_signal_bio_complex_via_target():
    """제목에 시설명사 없어도 stage1 target + 약한동사면 인정 (배곧 바이오단지 조성)."""
    it = _it("프레스티지바이오로직스, 원료약 위탁생산 계약",
             "시흥 배곧지구 바이오의약품 복합연구개발단지 조성 추진. 2조2000억원 투자.",
             ["action:조성,투자", "target:단지,원료의약품"])
    assert R._news_construction_signal(it) is True


# ── 공급사 게이트 ───────────────────────────────────────────────────────────

def test_supplier_busduct_gated():
    """부품(버스덕트) 공급사 PR = 시설 발주처 아님 → 공급사 판정 (가온전선).

    본문에 고객사 '데이터센터 증설' 이 있어도 면제되면 안 됨 (제목 기준 판정).
    """
    it = _it("가온전선, 美 생성형 AI 데이터센터에 버스덕트 첫 공급",
             "버스덕트를 공급하는 계약을 체결했다. 향후 데이터센터 증설에 따른 추가 수주 기대.")
    assert R._news_is_component_supplier(it) is True


def test_supplier_own_plant_not_gated():
    """제목에서 본인이 공장을 짓는 기사면 공급사 아님 (리드 유지)."""
    it = _it("OO전선, 구미에 전선 공장 착공…1000억 투자",
             "전선 생산을 위한 신공장을 착공했다.")
    assert R._news_is_component_supplier(it) is False


def test_facility_owner_not_supplier():
    """공급사 부품 키워드가 제목에 없으면 공급사 아님 (앰코 공장 증설)."""
    it = _it("앰코, 광주에 반도체 후공정 공장 증설", "")
    assert R._news_is_component_supplier(it) is False


# ── 토큰 파서 ───────────────────────────────────────────────────────────────

def test_news_targets_parse():
    it = _it("t", "c", ["action:증설,투자", "target:공장,후공정", "money:1조"])
    assert R._news_targets(it) == ["공장", "후공정"]
    assert R._news_actions(it) == ["증설", "투자"]
