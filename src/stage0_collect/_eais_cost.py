"""EAIS 인허가 → 추정공사비 환산 + 영업 가치 분류기.

자이씨앤에이 시공 영업팀이 원하는 신호는 "큰 공사" — 인허가 단계에서
정확한 도급액은 모르지만 (mainPurpsCdNm × totArea) 로 추정 가능.

단가 (만원/㎡) 는 업계 평균 시공비 — 보수적으로 잡았음:
  - CR 반도체 클린룸: 1,500
  - 이차전지 제조: 1,200
  - 제약 GMP: 600
  - R&D 연구시설: 400
  - 식품/음료 가공: 250
  - 일반공장/창고: 150
  - 발전/위험물: 800 (특수)

450억 임계값 환산:
  - CR  →  3,000㎡ +
  - 이차전지 → 3,750㎡ +
  - 제약 → 7,500㎡ +
  - R&D → 11,250㎡ +
  - 식품 → 18,000㎡ +
  - 일반공장 → 30,000㎡ +

주의: 단가는 BARE 시공만 — 토목·MEP·CR 설비 별도. 실제 도급액보다
보수적이므로, 450억 임계값을 통과한 건은 실제로는 더 큰 공사일 가능성 多.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# (카테고리, 만원/㎡ 단가)
COST_TABLE: list[tuple[str, int]] = [
    ("CR",        1500),
    ("이차전지",  1200),
    ("발전/위험물", 800),
    ("제약/바이오", 600),
    ("R&D",        400),
    ("식품/음료",  250),
    ("일반생산",   150),
    ("기타",         0),   # 명시적 컷 + 미분류 폴백 — 단가 0 으로 무조건 임계값 미달
]
COST_BY_CATEGORY: dict[str, int] = dict(COST_TABLE)

# 화이트리스트 yaml 카테고리 → 단가 카테고리 매핑.
# industrial_dongs.csv 의 categories 컬럼 값 → COST_BY_CATEGORY 키
DONG_CATEGORY_MAP: dict[str, str] = {
    "반도체_디스플레이": "CR",
    "이차전지":         "이차전지",
    "제약_바이오":       "제약/바이오",
    "연구개발":         "R&D",
    "식품":            "식품/음료",
    "일반제조":         "일반생산",
}

# 학교/학원/공공 키워드 — '교육연구시설' false positive 컷 대상.
# 강남·서초 R&D 화이트리스트 동 안에서 실제로는 학교·학원·공공 R&D 인 케이스 多.
# (CLAUDE.md: 공공 인프라 제외 대상)
SCHOOL_KEYWORDS: tuple[str, ...] = (
    "초등학교", "중학교", "고등학교", "대학교", "유치원",
    "어린이집", "특수학교", "학원", "교육청",
    # 확장: 청주대·금오공대·폴리텍구미캠퍼스 false positive 컷
    "대학", "캠퍼스", "폴리텍", "전문대", "교육원",
    "교육연구원",                      # ○○도교육연구원 — 학교 부속 (공공)
    "아카데미",                       # 이노베이션 아카데미 등
    "혁신파크", "혁신센터", "창업센터",   # 공공 R&D / 창업지원시설
    "복지",                           # 복지시설
    "도서관", "박물관", "문화센터",       # 공공문화시설
)

# 공공 인프라 — 영업 X 대상 (CLAUDE.md 명시: 주택/관급/공공인프라 제외).
# 하수처리장·소각장·자원순환 등은 동 시그널 적용해도 영업 가치 0.
PUBLIC_INFRA_KEYWORDS: tuple[str, ...] = (
    "하수처리", "오수처리", "정수장", "물재생",
    "분뇨처리", "쓰레기처리", "소각장", "재활용센터",
    "폐기물처리", "환경사업소", "위생처리", "위생매립",
    "공공청사", "관공서", "주민센터", "시청사", "구청사",
    "보건소", "소방서", "경찰서",
)
PUBLIC_INFRA_PURPOSES: tuple[str, ...] = (
    "자원순환관련시설",
)

# 식품 회사 — 동 시그널이 제약/CR 단가 부여해도 회사명이 명백한 식품이면 식품 단가로.
# (안산 CJ제일제당 → 제약_바이오 동 시그널 → 2,336억 false positive 케이스)
FOOD_COMPANY_KEYWORDS: tuple[str, ...] = (
    # 영문/약칭 표기
    "CJ제일제당", "CJ푸드빌",
    # 한글 표기 — EAIS 응답은 한글 회사명으로 들어오는 경우 多
    "씨제이제일제당", "씨제이푸드빌",
    "오뚜기", "농심", "삼양식품", "(주)대상", "대상㈜",
    "동원F&B", "동원에프앤비", "동원산업", "풀무원", "롯데웰푸드", "롯데칠성",
    "오리온", "빙그레", "남양유업", "매일유업", "서울우유",
    "SPC", "에스피씨", "파리바게뜨", "롯데푸드", "하림", "팔도", "동서식품",
    "샘표", "한국야쿠르트", "야쿠르트", "정식품", "사조",
    "오비맥주", "하이트진로", "롯데주류",
)
# 명시적 식품/음료 시설 키워드 — 동 시그널 무력화 트리거
FOOD_FACILITY_KEYWORDS: tuple[str, ...] = (
    "식품공장", "식품제조", "음료공장", "식품가공", "식품제조시설",
    "유가공시설", "주류제조", "음료제조", "제과공장", "제빵공장",
)

# '교육연구시설' 안에서 진짜 기업 R&D 만 골라내는 강한 키워드.
# 빌딩명에 이게 있어야 R&D 단가 부여, 아니면 "기타" (학교 폴백).
STRONG_RND_KEYWORDS: tuple[str, ...] = (
    "연구소", "연구원",                  # ETRI 등 정부·기업 R&D (학교는 위에서 컷)
    "R&D센터", "R&D빌딩", "R&D타워", "R&D",
    "테크센터", "테크노밸리", "기술원", "연구개발센터",
    "이노베이션센터", "R&D Center", "Research",
)

# 종합병원·의료원·보건소 — 자이씨앤에이 시공 영업 영역 X.
# '의료시설' 용도 안에 GMP 제조시설(제약/CDMO) 과 일반 종합병원이 섞여 있음.
# 일반 병원은 컷, 제약 제조시설은 유지 (HOSPITAL_KEYWORDS vs 명시적 제약 키워드).
HOSPITAL_KEYWORDS: tuple[str, ...] = (
    "종합병원", "의료원", "보건소", "병원",
    "의과대학", "의대",
    "재활원", "요양원", "요양병원", "정신병원",
    "치과병원", "한방병원", "한의원",
)
# '병원' 키워드라도 제약 제조시설로 봐야하는 예외 — CDMO·바이오 제조 키워드 있으면.
PHARMA_PROD_OVERRIDE_KEYWORDS: tuple[str, ...] = (
    "CDMO", "cGMP", "GMP", "원료의약", "완제의약",
    "백신", "세포치료", "유전자치료",
)

# 단순 비품·부품·자재 보관 시설 — 면적은 크지만 영업가치 X.
# 거제 동문 비품창고 (~98만㎡, 14,672억 일반생산 추정) 같은 케이스 컷.
NOISE_BLDNM_KEYWORDS: tuple[str, ...] = (
    "비품창고", "비품 창고", "비품보관", "비품 보관",
    "부품창고", "부품 창고",
    "자재창고", "자재 창고", "자재장", "자재 장",
    "야적장", "적치장",
    "차고", "주차장", "주차타워",
    "폐기물보관", "폐기물 보관",
    "임시건축물", "가설건축물",
)

# 동 카테고리 시그널 적용 대상 — '산업 전용' 용도만.
# 근린생활시설/업무시설/교육연구시설 등 도심·서비스 용도는 동 시그널 적용 X
# (강남 R&D 동 안의 상가복합 건물이 R&D 단가로 잘못 통과되는 문제 방지).
# 창고시설 제외 — CR 동의 일반 물류창고가 CR 단가 받는 false positive 방지.
INDUSTRIAL_PURPOSES_FOR_DONG_SIGNAL: tuple[str, ...] = (
    "공장", "위험물", "발전시설",
)


def _is_industrial_purpose_for_dong(main_purps: str) -> bool:
    return any(p in (main_purps or "") for p in INDUSTRIAL_PURPOSES_FOR_DONG_SIGNAL)


def _is_public_infra(main_purps: str, bld_nm: str) -> bool:
    """공공 하수/소각/자원순환/관공서 시설 — 영업 대상 X."""
    text = f"{main_purps or ''} {bld_nm or ''}"
    if any(kw in text for kw in PUBLIC_INFRA_KEYWORDS):
        return True
    if any(p in (main_purps or "") for p in PUBLIC_INFRA_PURPOSES):
        return True
    return False


def _has_food_company_signal(bld_nm: str, plat_plc: str = "") -> bool:
    """건물명/주소에 식품 회사 또는 식품시설 키워드 — 동 시그널 무력화 트리거."""
    text = f"{bld_nm or ''} {plat_plc or ''}"
    if any(kw in text for kw in FOOD_COMPANY_KEYWORDS):
        return True
    if any(kw in text for kw in FOOD_FACILITY_KEYWORDS):
        return True
    return False


def _has_strong_rnd_signal(bld_nm: str) -> bool:
    """건물명에 강한 R&D 키워드 — '교육연구시설' false positive 안전장치."""
    return any(kw in (bld_nm or "") for kw in STRONG_RND_KEYWORDS)


def _is_hospital(main_purps: str, bld_nm: str) -> bool:
    """의료시설 main_purps — 거의 다 병원·요양시설·의료법인 시설.

    실제 EAIS 캐시 데이터 검증 결과 (2026-05-22):
      - 의료시설 445건 中 病院 키워드 매칭 235건 + bldNm 빈값/주소형 209건
      - 제약 GMP 제조시설은 의료시설 main_purps 로 안 올라옴 (공장으로 올라옴)
    → 의료시설 main_purps 면 무조건 컷. 단, bldNm 에 강한 제약 제조 시그널
      (CDMO/GMP/원료의약/완제의약/세포치료 등) 있으면 예외로 유지.
    """
    if "의료시설" not in (main_purps or ""):
        return False
    # bldNm 에 강한 제약 제조 시그널 있으면 제약/바이오 유지 (override)
    name = bld_nm or ""
    if name and any(kw in name for kw in PHARMA_PROD_OVERRIDE_KEYWORDS):
        return False
    # 그 외 모두 컷 (HOSPITAL_KEYWORDS 매칭 안 돼도, bldNm 빈값이어도)
    return True


def _is_noise_bldnm(bld_nm: str) -> bool:
    """비품창고·자재장·주차장 등 영업가치 0 인 빌딩명."""
    return any(kw in (bld_nm or "") for kw in NOISE_BLDNM_KEYWORDS)

# 추정공사비 임계값 (만원 단위)
DEFAULT_THRESHOLD_MAN: int = 450 * 10000   # 450억 = 4,500,000 만원

# 추정공사비 상한 (만원) — 영업 타겟 '450억 ~ 2조 미만' (2026-06-02: 1조→2조 상향).
# 2조(=200,000,000 만원) 이상은 단일 건물 인허가가 아니라 부지·단지 totArea 가
# 통째로 잡힌 데이터 아티팩트로 보고 컷.
# ※ 트레이드오프: 1조~2조 구간을 열면 '거제 아주동 ~99만㎡ → 14,837억' 같은 산단 합산
#    아티팩트가 일부 새어들 수 있다(연면적 100만㎡ 미만이라 아래 area outlier 컷에 안 걸림).
#    노이즈가 늘면 연면적 outlier 임계(eais.py 의 100만㎡)를 함께 낮추는 걸 검토할 것.
MAX_COST_MAN: int = 20000 * 10000   # 2조 = 200,000,000 만원

# 카테고리 추론 — 키워드 매칭. 우선순위 上→下 (먼저 매치된 게 win).
# 인허가 응답 (mainPurpsCdNm + bldNm + platPlc) 합쳐서 검색.
CATEGORY_KEYWORDS: list[tuple[str, list[str]]] = [
    ("CR", [
        "반도체", "디스플레이", "OLED", "LCD", "패널", "웨이퍼", "메모리",
        "팹", "fab", "Fab", "FAB", "클린룸", "Clean Room",
    ]),
    ("이차전지", [
        "이차전지", "2차전지", "배터리", "양극재", "음극재", "분리막",
        "전해질", "전해액", "셀제조", "battery", "Battery", "BATTERY",
        "LFP", "NCM", "NCA",
    ]),
    ("제약/바이오", [
        "제약", "바이오", "백신", "원료의약", "완제의약", "의약품",
        "세포치료", "유전자치료", "CDMO", "GMP", "cGMP", "병원",
        "의료", "진단", "혈장", "단클론", "항체",
    ]),
    # R&D 는 명확한 자체 키워드만 — "교육연구"/"교육연구시설" 은 학교·학원·일반상가도
    # 광범위하게 잡으므로 키워드에서 제외 (false positive 컷).
    # "교육연구시설" 단독은 → "기타" (100만원/㎡) 폴백 → 450억 통과 사실상 X.
    ("R&D", [
        "연구소", "연구시설", "연구개발", "R&D", "기술원",
        "테크센터", "테크노밸리",
    ]),
    ("식품/음료", [
        "식품", "음료", "주류", "유가공", "제과", "제빵",
        "축산물", "수산물", "조미료", "건강기능식품", "HACCP",
    ]),
    ("발전/위험물", [
        "발전시설", "발전소", "위험물", "LNG", "LPG", "수소",
        "변전", "송전", "에너지저장",
    ]),
    ("일반생산", [
        "공장", "제조", "가공", "조립", "산업시설", "제1종근린생활시설",
        "제2종근린생활시설",
    ]),
    # 창고는 별도 — 단순 물류창고는 공사비 낮음, 그래도 일반생산 단가로 측정
    ("일반생산", ["창고", "물류", "저장시설", "보관시설"]),
]


def _is_school(main_purps: str, bld_nm: str) -> bool:
    """교육연구시설인데 학교/유치원 등이면 True — R&D 단가 부여하면 안 됨."""
    if "교육연구" not in (main_purps or ""):
        return False
    name = bld_nm or ""
    return any(kw in name for kw in SCHOOL_KEYWORDS)


def infer_category(
    main_purps: str,
    bld_nm: str = "",
    plat_plc: str = "",
    dong_categories: list[str] | None = None,
) -> str:
    """인허가 데이터에서 카테고리 추론.

    우선순위:
      0a) 공공 인프라 컷 (하수/소각/자원순환/관공서) → "기타"
      0b) 학교 컷 ('교육연구' + 학교 키워드) → "기타"
      0c) 식품 회사 시그널 (bldNm/plat_plc) → "식품/음료"  (동 시그널 우선)
      0d) '교육연구시설' 안전장치 — 강한 R&D 키워드 없으면 "기타"
      1) 동의 산업 클러스터 정보 (dong_categories) — yaml 화이트리스트 기반
      2) 응답 텍스트 키워드 매칭 (main_purps + bld_nm + plat_plc)
      3) 매칭 없으면 "기타"
    """
    # 0a) 공공 인프라 컷 — 영업 대상 X
    if _is_public_infra(main_purps, bld_nm):
        return "기타"

    # 0b) 학교 컷
    if _is_school(main_purps, bld_nm):
        return "기타"

    # 0b') 노이즈 빌딩명 — 비품/자재/주차/야적장 등 (거제 동문 비품창고 케이스)
    if _is_noise_bldnm(bld_nm):
        return "기타"

    # 0b'') 일반 종합병원 — 영업 영역 X (단 CDMO·GMP 제약 제조는 유지)
    if _is_hospital(main_purps, bld_nm):
        return "기타"

    # 0c) 식품 회사 / 식품 시설 — 동·키워드 시그널보다 우선
    #     (안산 CJ제일제당 케이스: 제약_바이오 동 시그널 무력화)
    if _has_food_company_signal(bld_nm, plat_plc):
        return "식품/음료"

    # 0d) 교육연구시설 안전장치 — substring "연구시설" 우회 차단.
    #     bldNm 에 강한 R&D 키워드 있을 때만 R&D, 아니면 "기타".
    if "교육연구" in (main_purps or ""):
        if _has_strong_rnd_signal(bld_nm):
            return "R&D"
        return "기타"

    # 1) 동 카테고리 시그널 — 동이 반도체 클러스터면 거기 '공장' 인허가는 CR 단가.
    #    단, 산업 전용 용도일 때만 (근생/업무/교육연구시설/창고는 제외).
    if dong_categories and _is_industrial_purpose_for_dong(main_purps):
        # 우선순위 높은 산업부터 (단가 큰 순)
        for cat in ("반도체_디스플레이", "이차전지", "제약_바이오", "연구개발", "식품", "일반제조"):
            if cat in dong_categories:
                return DONG_CATEGORY_MAP[cat]

    # 2) 키워드 매칭 — 동 카테고리 정보 없을 때만 폴백
    haystack = " ".join([main_purps or "", bld_nm or "", plat_plc or ""])
    if not haystack.strip():
        return "기타"
    for category, keywords in CATEGORY_KEYWORDS:
        for kw in keywords:
            if kw in haystack:
                return category
    return "기타"


def _to_float(v) -> Optional[float]:
    """API 응답 값 → float. None/공백/"0" 은 None 으로."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v else None
    s = str(v).strip().replace(",", "")
    if not s or s in ("0", "0.0"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def estimate_cost_man(
    main_purps: str,
    tot_area: float | str | None,
    bld_nm: str = "",
    plat_plc: str = "",
    dong_categories: list[str] | None = None,
) -> tuple[int, str, Optional[float]]:
    """추정공사비 (만원), 카테고리, 사용 연면적(㎡) 반환.

    dong_categories: 해당 동이 속한 산업 클러스터 (industrial_dongs.csv 의 categories).
                     주어지면 단가 추정에 1차 신호로 사용.
    연면적 누락 시 (0, category, None) 반환 — 필터 통과 못 함.
    """
    category = infer_category(main_purps, bld_nm, plat_plc, dong_categories)
    area = _to_float(tot_area)
    if area is None or area <= 0:
        return 0, category, None
    unit = COST_BY_CATEGORY.get(category, 100)
    cost_man = int(round(area * unit))
    return cost_man, category, area


def passes_threshold(
    cost_man: int,
    threshold_man: int = DEFAULT_THRESHOLD_MAN,
) -> bool:
    """추정공사비가 임계값 (기본 450억) 이상이면 True."""
    return cost_man >= threshold_man


def format_cost(cost_man: int) -> str:
    """만원 → '4,500억' / '32억 5,000' 식 표기."""
    if cost_man <= 0:
        return "추정불가"
    if cost_man >= 10000:
        eok = cost_man // 10000
        man = cost_man % 10000
        if man == 0:
            return f"{eok:,}억"
        return f"{eok:,}억 {man:,}"
    return f"{cost_man:,}만"


if __name__ == "__main__":
    # 셀프 테스트
    cases = [
        ("공장", 50000, "삼성전자 P5 클린룸"),
        ("공장", 50000, "LG에너지솔루션 청주공장"),
        ("연구소", 30000, "셀트리온 R&D센터"),
        ("창고", 100000, "쿠팡 메가 풀필먼트"),
        ("공장", 5000, "일반 중소공장"),
        ("제2종근린생활시설", 200, "소규모 공장"),
    ]
    for purp, area, bldnm in cases:
        cost, cat, used_area = estimate_cost_man(purp, area, bldnm)
        ok = "✓" if passes_threshold(cost) else "✗"
        print(f"{ok} [{cat:8s}] {bldnm:30s} {area}㎡ → {format_cost(cost)}")
