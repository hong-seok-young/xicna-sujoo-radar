"""자이씨앤에이 수주레이더 — 시설 카테고리 분류기.

각 기사/공시/입찰을 6개 시설 카테고리로 멀티태깅:
  1. CR (반도체)
  2. 일반생산
  3. 제약/바이오
  4. 식품/음료
  5. R&D
  6. 이차전지

규칙:
  - 제목 매칭 = 2점, 본문 매칭 = 1점 (제목 가중치 2배)
  - 카테고리별 점수 ≥ 1 이면 해당 카테고리 태깅 (복수 가능)
  - 어디에도 안 걸리면 ["기타"]
  - 키워드는 공백·특수문자 제거 후 부분일치 (case-insensitive)

사용:
    from src.common.categorize import tag, score
    tags = tag(title, content)            # → ["CR", "R&D"]
    scores = score(title, content)        # → {"CR": 4, "R&D": 2, "일반생산": 0, ...}
"""
from __future__ import annotations

import re

# 카테고리별 키워드 — 사용자 제공 리스트 기반
# 명시적 # 접두사는 제거됨. 공백·특수문자는 매칭 시 정규화로 처리.
CATEGORIES: dict[str, list[str]] = {
    "CR": [
        "반도체클러스터", "FAB", "클린룸", "패키징공장", "후공정", "전공정",
        "EUV", "첨단패키징", "테스트베드", "R&D캠퍼스", "생산라인증설",
        "웨이퍼", "반도체공장", "파일럿라인", "양산라인",
        # 보강
        "반도체", "팹", "포토공정", "식각공정", "증착공정",
    ],
    "일반생산": [
        "일반생산시설", "공장신축", "생산공장", "스마트팩토리", "자동화설비",
        "물류창고", "물류센터", "냉동물류센터", "산업단지", "공장증설",
        "설비증설", "제조시설", "생산라인", "공장부지", "CAPEX",
        "반도체장비클린룸", "웨이퍼공장신설", "전구체생산시설", "소부장공장투자",
        "반도체부품공장",
        # 보강
        "신공장", "제2공장", "제3공장", "제조공장", "제조라인",
    ],
    "제약/바이오": [
        "제약시설", "제약바이오", "GMP", "CGMP", "BGMP",
        "바이오플랜트", "백신공장", "세포배양시설", "완제공장",
        "원료의약품공장", "의약품생산시설", "파일럿플랜트",
        "QC센터", "QA센터", "동물실험시설", "무균실",
        # 보강
        "바이오의약품", "세포치료제", "유전자치료제", "CDMO",
        "원료의약품", "의약품공장", "바이오공장",
    ],
    "식품/음료": [
        "식품공장", "HACCP", "냉장물류", "콜드체인", "생산동",
        "자동화라인", "식품제조시설", "물류허브", "음료공장",
        "가공시설", "품질시험동", "신규공장",
        # 보강
        "식음료공장", "주류공장", "유제품공장", "조미료공장", "제과공장",
    ],
    "R&D": [
        "R&D센터", "연구소", "기술연구원", "융합연구시설", "실험시설",
        "테스트베드", "파일럿플랜트", "산학연구시설", "연구캠퍼스",
        "혁신센터", "실증센터", "국책연구시설",
        # 보강
        "연구개발센터", "R&D캠퍼스", "기술센터", "이노베이션센터",
    ],
    "이차전지": [
        "기가팩토리", "배터리셀공장", "양극재공장", "음극재공장",
        "분리막공장", "전해질공장", "리사이클링플랜트", "ESS시설",
        "전구체공장",
        # 보강
        "배터리공장", "이차전지", "배터리셀", "양극재", "음극재",
        "분리막", "전해질", "LFP", "NCM", "리튬이온",
    ],
}

# 카테고리 표시 순서 (UI 일관성)
CATEGORY_ORDER = ["CR", "일반생산", "제약/바이오", "식품/음료", "R&D", "이차전지", "기타"]

# 카테고리별 색상 코드 (HTML 칩용)
CATEGORY_COLORS = {
    "CR":       "#7cc4ff",  # blue
    "일반생산":   "#b8c1cf",  # silver
    "제약/바이오": "#e07b6f",  # red
    "식품/음료":  "#5ec77a",  # green
    "R&D":      "#c87cff",  # purple
    "이차전지":   "#ffc857",  # gold
    "기타":      "#5a6473",  # gray
}

# 정규화: 공백/특수문자 제거 후 매칭 (대소문자 무시)
_NORM_RE = re.compile(r"[\s·ㆍ・\-_/\\,()\[\]{}'\"]+")


def _normalize(s: str) -> str:
    """매칭용 정규화: 소문자화 + 공백/특수문자 제거."""
    if not s:
        return ""
    return _NORM_RE.sub("", s.lower())


# 키워드도 정규화해서 사전 빌드 (런타임 절약)
_NORMALIZED_KEYWORDS: dict[str, list[str]] = {
    cat: sorted({_normalize(kw) for kw in kws if kw.strip()}, key=len, reverse=True)
    for cat, kws in CATEGORIES.items()
}


def score(title: str, content: str) -> dict[str, int]:
    """카테고리별 점수 산출.

    제목 매칭당 +2점, 본문 매칭당 +1점. 한 카테고리 안에서 여러 키워드가
    매칭되면 점수가 누적됨 (강한 시그널일수록 점수가 높아져 정렬·랭킹 가능).
    """
    norm_title = _normalize(title)
    norm_body = _normalize(content)
    result: dict[str, int] = {}
    for cat, kws in _NORMALIZED_KEYWORDS.items():
        s = 0
        for kw in kws:
            if not kw:
                continue
            if kw in norm_title:
                s += 2
            if kw in norm_body:
                s += 1
        result[cat] = s
    return result


def tag(title: str, content: str, threshold: int = 1) -> list[str]:
    """카테고리 멀티태깅. score >= threshold 인 카테고리 모두 반환.

    매칭 없으면 ["기타"]. 점수 내림차순 정렬.
    """
    scores = score(title, content)
    matched = [(cat, s) for cat, s in scores.items() if s >= threshold]
    if not matched:
        return ["기타"]
    matched.sort(key=lambda x: (-x[1], CATEGORY_ORDER.index(x[0]) if x[0] in CATEGORY_ORDER else 999))
    return [cat for cat, _ in matched]


def color_for(category: str) -> str:
    """카테고리명 → hex 색상."""
    return CATEGORY_COLORS.get(category, "#5a6473")


if __name__ == "__main__":
    # 셀프 테스트
    samples = [
        ("삼성전자, 평택 P5 FAB 착공 — 첨단패키징 라인 증설", "EUV 노광장비 도입, 클린룸 신설"),
        ("LG에너지솔루션 오창 배터리셀공장 2조 투자", "양극재 라인 증설"),
        ("셀트리온 송도 바이오플랜트 완제공장 신축 결정", "GMP 인증, 백신 생산"),
        ("CJ제일제당 진천 식품공장 증설", "HACCP 라인 추가"),
        ("KAIST 대전 R&D센터 착공", "융합연구시설"),
        ("삼성바이오로직스 5공장 + R&D센터", "GMP, 연구소 통합 단지"),
        ("XX전자 아파트 분양", "단순 부동산"),
    ]
    for t, c in samples:
        print(f"\n제목: {t}")
        print(f"  → 태그: {tag(t, c)}")
        print(f"  → 점수: {{ {', '.join(f'{k}:{v}' for k, v in score(t, c).items() if v > 0)} }}")
