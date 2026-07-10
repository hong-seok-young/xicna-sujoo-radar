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


# ── 이벤트 단위 중복제거 ─────────────────────────────────────────────────────

def _row(title, content="", patterns=None, source="news_high"):
    return ("", {"title": title, "content": content, "url": "http://x/" + title[:6],
                 "source": "x.com", "stage1_matched_patterns": patterns or []}, source)


def _key(title, content=""):
    it = {"title": title, "content": content}
    return (R._dedup_title_tokens(title), R._dedup_topic_sig(it))


def test_dedup_topic_sig_honam_cluster():
    """삼성·SK 호남 반도체 공장 = 제목에 플레이어+지역+산업+시설 모두 → 토픽 시그니처."""
    it = {"title": "삼성·SK하이닉스, 호남 반도체 공장 검토…비수도권 투자 속도", "content": ""}
    assert R._dedup_topic_sig(it) == ("호남권", "반도체", frozenset({"삼성", "SK"}))


def test_dedup_weak_sig_not_merged_into_player_cluster():
    """앰코는 대형 플레이어 목록 밖 → 약한 프로젝트 시그니처(players=None, 2026-07-10).
    지역·산업이 같아도 삼성·SK 강한 클러스터(플레이어 명시)와는 병합 금지(강-약 병합 안 함)."""
    amkor = {"title": "앰코, 광주에 1조 투자 검토…반도체 후공정 공장 증설", "content": ""}
    assert R._dedup_topic_sig(amkor) == ("호남권", "반도체", None)   # 약한 시그니처
    samsung = {"title": "삼성·SK하이닉스, 호남 반도체 공장 검토", "content": ""}
    ka = (R._dedup_title_tokens(amkor["title"]), R._dedup_topic_sig(amkor))
    kb = (R._dedup_title_tokens(samsung["title"]), R._dedup_topic_sig(samsung))
    assert R._dedup_same_event(ka, kb) is False


def test_dedup_topic_sig_none_when_industry_absent_in_title():
    """제목에 산업(반도체)이 없으면 토픽 시그니처 없음 — 같은 회사·지역의 다른 사건과 안 묶임."""
    it = {"title": "청주 SK하이닉스서 화학물질 접촉사고…작업자 2명 병원 이송", "content": ""}
    assert R._dedup_topic_sig(it) is None


def test_dedup_merges_near_identical_titles():
    """통신사 받아쓰기 등 거의 동일한 제목 → 같은 사안."""
    ka = _key("가온전선, 美 생성형 AI 데이터센터에 버스덕트 첫 공급")
    kb = _key("가온전선, 미국 생성형 AI 데이터센터에 ‘버스덕트’ 공급")
    assert R._dedup_same_event(ka, kb) is True


def test_dedup_does_not_merge_distinct_leads():
    """앰코(광주 후공정)와 삼성·SK 호남 클러스터는 다른 건 → 병합 금지."""
    assert R._dedup_same_event(
        _key("앰코, 광주에 1조 투자 검토…반도체 후공정 공장 증설"),
        _key("삼성·SK하이닉스, 호남 반도체 공장 검토"),
    ) is False


def test_dedup_does_not_merge_different_events_same_company():
    """같은 회사(SK)라도 사고 vs 양산은 다른 사건 → 병합 금지."""
    assert R._dedup_same_event(
        _key("청주 SK하이닉스서 화학물질 접촉사고"),
        _key("하이닉스, 연말 375단 낸드 양산…몰리브덴 첫 도입"),
    ) is False


def test_dedup_sections_collapse_and_preserve():
    """호남 클러스터 3건은 대표 1건(+2 dups)으로 접히고, 앰코는 별도 유지."""
    high = [
        _row("삼성·SK하이닉스, 호남 반도체 공장 검토", patterns=["target:공장"]),
        _row("삼성전자 반도체 공장 호남으로…정부 회의 개최", patterns=["target:공장"]),
        _row("전남도, 삼성·SK에 호남 반도체 클러스터 구축 촉구", patterns=["target:공장"]),
        _row("앰코, 광주 반도체 후공정 공장 증설", patterns=["action:증설", "target:공장"]),
    ]
    H, _Mi, _Lo = R._dedup_news_sections(high, [], [])
    titles = [it["title"] for _, it, _ in H]
    assert len(H) == 2                               # 호남 대표 1 + 앰코 1
    assert any("앰코" in t for t in titles)           # 앰코 보존(별도)
    honam = [it for _, it, _ in H if "앰코" not in it["title"]][0]
    assert len(honam.get("_dups", [])) == 2          # 나머지 2건은 dups 로 보존(정보 유지)


# ── 섹션 라벨 상한 / 상업가동 / 매출목표 (2026-07-10 P0~P1) ──────────────────
# 2026-07-10 리포트: classify_rss 가 MID/LOW 판정한 기사가 _news_score 에서 시설점수+규모만으로
# A/S 재진입해 '뉴스 HIGH(선제 접촉)'를 오염(테슬라 투자후퇴·러트닉 촉구·에코프로 유상증자 등).
# 원칙: 오탐은 삭제가 아니라 참조탭으로 강등(재현율 우선). 진짜 리드(착공)는 HIGH 보존.

_SECTION = {"S": "HIGH", "A": "HIGH", "B": "MID", "C": "LOW"}


def _route(title: str, content: str = "", patterns=None) -> dict:
    """리포트 파이프라인(build 3500~3511) 재현 → 최종 라벨/등급/섹션."""
    it = _it(title, content, patterns)
    lab, _rea = R.classify_rss(it)
    if lab == "HIGH" and R._news_out_of_scope(it):
        lab = "MID"
    sc = R._news_score(it, R._news_source_for(lab))
    return {"label": lab, "grade": sc["grade"], "score": sc["score"],
            "section": _SECTION[sc["grade"]]}


def test_negative_framing_demoted():
    """투자 후퇴·비판 프레이밍(테슬라 '혈세·후퇴·미스터리')은 시설점수가 있어도 노이즈 강등.
    (2026-07-10 균형 패스: news_mid 블랭킷 상한 대신 타깃 노이즈 게이트로 처리)"""
    r = _route("10년간 1조 혈세 지원(테슬라)했는데 고용·투자·기부 '후퇴'...영업익 1%의 미스터리",
               "테슬라코리아 국민 혈세 1조원. 기가팩토리 투자는 1424억원 수준.",
               ["action:투자", "target:기가팩토리", "money:1424억"])
    assert r["section"] != "HIGH" and r["grade"] == "C"


def test_political_urging_demoted():
    """정치·로비성(러트닉 美상무 '촉구')은 시설·규모가 있어도 노이즈 강등 — 실제 발주 아님."""
    r = _route('"러트닉 美상무, 삼성·SK 미국 내 메모리 생산 확대 촉구"',
               "러트닉 상무장관이 삼성·SK에 미국 내 생산 확대를 촉구. 마이크론 공장 언급.",
               ["action:투자", "target:공장", "money:1000억"])
    assert r["section"] != "HIGH"


def test_recall_weak_action_big_facility_stays_high():
    """약한 행동(투자)이라도 강한 시설+큰 금액이면 HIGH 유지 — 에어리퀴드 3천억 같은 진짜 리드 보존.
    (news_mid 블랭킷 상한 제거의 핵심 목적: 리드를 MID 로 매장하지 않는다.)"""
    r = _route("에어리퀴드, SK하이닉스와 3천억원 대규모 투자 발표",
               "SK하이닉스 반도체 팹에 산업가스 공급 위한 3천억원 규모 신규 공장 투자.",
               ["action:투자", "target:공장", "money:3천억"])
    assert r["section"] == "HIGH"


def test_p0_body_noise_capped_to_C():
    """본문 노이즈(주가·유상증자 ≥2)로 LOW 판정된 기사 → C 강등 (news_low 상한 39)."""
    r = _route("에코프로비엠, 끝내 주주에 손 벌린 이유",
               "1조2000억원대 증자 추진. 유상증자로 양극재 시설 투자 재원 2000억. 지분 취득.",
               ["action:투자,매입", "target:시설,양극재", "money:2000억"])
    assert r["label"] == "LOW" and r["grade"] == "C" and r["score"] <= 39


def test_p1_revenue_target_headline_demoted():
    """'매출 N억 정조준' = 소재·부품사 판매확대 기사 → HIGH 박탈 (삼양사)."""
    r = _route('삼양사, 반도체 초순수 핵심 이온교환수지…"매출 1200억원 정조준"',
               "이온교환수지 매출을 1200억원까지 확대 목표. 데이터센터 투자로 반도체 팹 증설.",
               ["action:증설,투자", "target:데이터센터,팹", "money:1200억"])
    assert r["section"] != "HIGH"


def test_p1_commercial_operation_demoted():
    """'상업가동' = 다 지어진 공장 → 시공 완료로 LOW (엘앤에프 새만금)."""
    r = _route("엘앤에프, LS와 전구체 내재화 속도…새만금 공장 4분기 상업가동",
               "새만금 국가산업단지 내 NCM 전구체 공장을 4분기 상업가동한다.",
               ["action:수주,가동", "target:공장,시설,단지"])
    assert r["label"] == "LOW" and r["grade"] == "C"


def test_p1_commercial_op_with_new_construction_survives():
    """상업가동 언급이 있어도 신규 착공이 함께면 리드 유지 (over-cut 방지)."""
    r = _route("OO바이오, 1공장 상업가동…송도 제2공장 착공",
               "기존 1공장은 상업가동 중이며, 신규 제2공장을 착공한다.",
               ["action:착공,가동", "target:공장"])
    assert r["label"] != "LOW"


def test_recall_genuine_groundbreaking_stays_high():
    """실제 착공 리드(강한 행동+공장+면적)는 HIGH 유지 — 강등 로직이 리드를 죽이면 안 됨."""
    r = _route("도우인시스, 베트남 제2공장 착공",
               "베트남 제2공장(V2) 신축 착수. 착공식 개최. 7649평 UTG 생산능력 확대.",
               ["action:착공,신축,착수", "target:공장,생산능력", "area:7649평"])
    assert r["section"] == "HIGH"


# ── DART2 IT/전산장비·SW 공급계약 게이트 (2026-07-10 P2⑤) ───────────────────
# 데이타솔루션 'GPU 서버·HW/SW 공급'(삼성SDS 동탄DC) 4,381억 이 데이터센터 시설점수로 A 오르던 오탐.

def test_dart2_it_supply_facility_zeroed():
    """GPU·서버·HW/SW 공급 = 데이터센터 '건물' 시공 아님 → 시설점수 0 (A 진입 불가)."""
    it = {"title": "[데이타솔루션] 단일판매ㆍ공급계약체결",
          "content": "삼성SDS 동탄데이터센터 AI컴퓨팅자원 GPU 서버 외 AI인프라 구축용 HW,SW,Service 공급"}
    assert R._is_it_supply_contract(it) is True
    assert R._dart2_score(it)["breakdown"]["facility"] == 0


def test_dart2_construction_work_keeps_facility():
    """건축·설비 '공사' 표현이 있으면 IT 공급 아님 = 시설 시공 → 시설점수 유지."""
    it = {"title": "[OO건설] 데이터센터 신축공사",
          "content": "데이터센터 신축공사 및 기계설비 공사. 서버실 구축 포함."}
    assert R._is_it_supply_contract(it) is False


def test_dart2_semiconductor_supply_not_it():
    """반도체 장비·소재 공급(CR/소부장)은 IT 공급 게이트에 안 걸림 (시설점수 유지)."""
    it = {"title": "[OO소재] 반도체 특수가스 공급계약",
          "content": "반도체 팹에 특수가스 및 포토레지스트 공급."}
    assert R._is_it_supply_contract(it) is False


# ── 이벤트 중복제거 — 약한 프로젝트 클러스터 (2026-07-10 P2⑥) ────────────────

def test_dedup_merges_same_project_no_big_player():
    """대형 플레이어 목록 밖 기업이라도 같은 프로젝트(새만금 전구체 공장)면 매체별 보도 병합."""
    ka = _key("엘앤에프, LS와 전구체 내재화 속도…새만금 공장 4분기 상업가동")
    kb = _key("LS엘앤에프배터리솔루션, 새만금 전구체 공장 4분기 상업 가동")
    assert R._dedup_same_event(ka, kb) is True


def test_dedup_weak_cluster_distinct_projects_not_merged():
    """같은 지역·산업(호남 이차전지)이라도 다른 프로젝트(군산 양극재 vs 새만금 전구체)는 병합 금지."""
    ka = _key("코스모신소재, 군산 양극재 공장 착공")
    kb = _key("LS엘앤에프배터리솔루션, 새만금 전구체 공장 4분기 상업 가동")
    assert R._dedup_same_event(ka, kb) is False
