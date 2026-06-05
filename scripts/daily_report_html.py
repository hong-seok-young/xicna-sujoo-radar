"""자이씨앤에이 수주레이더 — 일일 통합 HTML 리포트.

scripts/daily_report.py 의 HTML 버전. self-contained (CDN/외부의존 없음),
브라우저에서 더블클릭으로 열기 가능.

사용법:
    python scripts/daily_report_html.py
    python scripts/daily_report_html.py --rss data/filtered/X.jsonl \\
                                       --g2b data/raw/g2b_X.jsonl \\
                                       --dart data/raw/dart_X.jsonl
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 프로젝트 루트를 sys.path 에 추가 (스크립트 직접 실행 시 src 모듈 import 위해)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.common.categorize import (  # noqa: E402
    CATEGORY_ORDER, CATEGORY_COLORS, tag as categorize_tag,
)
from src.common.scoring import score_opportunity, grade_for  # noqa: E402

KST = timezone(timedelta(hours=9))


# DART 매출 자동 fetch 로 빌드한 제약·바이오 회사 DB (data/cache/pharma_revenue.json).
# 매출 3,000억+ 또는 CDMO/바이오 신약사 = 500억+ 공장 발주 여력 회사.
# 빌드: python scripts/build_pharma_revenue_db.py --year 2024
def _load_pharma_revenue_db() -> dict[str, dict]:
    """회사명 → {revenue, reason, ...} 매핑. 파일 없으면 빈 dict."""
    p = _PROJECT_ROOT / "data" / "cache" / "pharma_revenue.json"
    if not p.exists():
        return {}
    try:
        import json as _j
        d = _j.loads(p.read_text(encoding="utf-8"))
        return d.get("companies") or {}
    except Exception:
        return {}


_PHARMA_DB: dict[str, dict] = _load_pharma_revenue_db()


def _normalize_corp_name(s: str) -> str:
    """회사명 매칭 정규화."""
    return (s or "").replace("(주)", "").replace("(유)", "") \
                   .replace("주식회사", "").replace(" ", "")


_PHARMA_NORM_KEYS: list[tuple[str, str]] = [
    (_normalize_corp_name(k), k) for k in _PHARMA_DB
]


def _match_pharma_company(bssh: str) -> tuple[str, dict] | None:
    """BSSH(MFDS 업체명) → DART 매출 DB 의 회사명 부분일치 매칭.

    예: BSSH='(주)대웅제약' → DART '대웅제약' 매칭. 매칭 시 (DART 회사명, info) 반환.
    """
    if not bssh:
        return None
    norm = _normalize_corp_name(bssh)
    # 1) 정확 일치 우선
    for k_norm, k_orig in _PHARMA_NORM_KEYS:
        if norm == k_norm:
            return k_orig, _PHARMA_DB[k_orig]
    # 2) 부분 일치 (DART 회사명이 MFDS 업체명에 포함)
    for k_norm, k_orig in _PHARMA_NORM_KEYS:
        if len(k_norm) >= 3 and k_norm in norm:
            return k_orig, _PHARMA_DB[k_orig]
    return None


def _is_major_pharma(bssh: str) -> bool:
    """매출 3,000억+ or CDMO 회사명 매칭 → True. 매칭 안 되면 카드 제외."""
    return _match_pharma_company(bssh) is not None

STRONG_ACTIONS = {"착공", "기공", "신축", "증설", "신설", "발주", "수주",
                  "낙찰", "기공식", "개소"}
# `확장`, `확충` 은 "사업확장/라인업확장" 노이즈 多 → WEAK 로 강등
WEAK_ACTIONS = {"체결", "추진", "검토", "획득", "지정", "선정", "취득", "양수", "투자",
                "확장", "확충"}
# 명백한 건설 액션 — 이게 매칭되면 산업 카테고리 미매칭(본문 짧아서)이어도 HIGH 유지.
# 이유: "파마리서치 강릉 제5공장 착공" 처럼 회사명/지명만으로 categorize 가 못 잡는
# 명백한 시공 시그널 케이스를 살리기 위함.
# 반대로 `신설`(조직 신설), `증설`(라인 증설) 은 비공사 케이스 多 — 카테고리 매칭 必須.
CONSTRUCTION_ACTIONS = {"착공", "기공", "기공식", "신축"}
# === 영업 골든타임 너머 — 시공 완료 신호 ===
# 자이씨앤에이는 시공사. 준공·완공은 시공이 끝났다는 뜻이라 영업 가치 없음.
# 단, 같은 기사에 STRONG_ACTIONS(착공/신축 등) 미래 시그널이 함께 있으면 혼합 기사
# (준공식에서 추가 증설 발표 등) 로 보아 HIGH 유지.
DONE_ACTIONS = {"준공", "완공", "준공식", "완공식"}
# `라인`, `시설`, `센터` 는 너무 일반적 (라인업·체험시설·문화센터) → STRONG 에서 제외.
# `CR` 두글자 단독 매칭은 본문 우연 매칭 多 → 클린룸 동시 등장 조건으로만 인정 (아래 로직).
STRONG_TARGETS = {"공장", "플랜트", "캠퍼스", "클린룸", "GMP", "CDMO",
                  "기가팩토리", "데이터센터", "물류센터", "물류창고",
                  "FAB", "팹", "바이오플랜트", "백신공장", "완제공장",
                  "원료의약품", "OSAT", "패키징", "후공정", "전공정",
                  "배터리셀", "양극재", "음극재", "분리막", "전해질",
                  "R&D센터"}
# WEAK_TARGETS — 단독으론 약함. 시공 action(CONSTRUCTION_ACTIONS)이 동반될 때만 인정.
# "연구소/연구원" 은 본문에 "OO연구원/연구소" 인용으로도 등장 多 → 시공 액션 동반 필수.
# "기지" 도 "전초기지·거점기지" 같은 비유 多.
WEAK_TARGETS = {"연구소", "연구원", "기지", "라인", "시설", "사옥", "단지"}
NOISE_KEYWORDS = ["주가", "목표가", "지주회사", "상장", "인수합병", "아파트", "매각",
                  "유상증자", "공모주", "코스피", "코스닥", "환율", "유가",
                  "재건축", "재개발", "분양", "청약", "치료센터", "병원",
                  # 소비재·유통·정책
                  "PB", "간편식", "도시락", "김밥", "라인업", "리뉴얼",
                  "신메뉴", "신상품", "골프", "여행", "관광", "사찰", "축제",
                  "국무원", "민용항공국", "백악관", "정부가", "당국이",
                  # 인사·조직·디지털전환 (산업 카테고리는 잡히지만 시공 무관)
                  "전진 배치", "인사발령", "조직개편", "임원 인사",
                  "AI 도입", "AX 전환", "디지털 전환", "DX 전환",
                  "사무 업무", "사무업무 90%",
                  "뒤처진 이유", "사각지대", "트렌드 분석", "시장 전망",
                  # 공사장 사고
                  "공사장서", "추락", "부상", "사망", "사고가 났", "사고로",
                  "고소작업대", "고소 작업대", "전진기지",
                  # === 90일치 AI 감사 후 신규 추가 (HIGH false positive 75%) ===
                  # 정부 정책·R&D 사업 (HIGH-001/003/004)
                  "고용위기", "선제대응", "기후부", "고용노동부", "산업통상부",
                  "산업연구원", "장관", "차관", "국장", "착수보고회", "착수회의",
                  "킥오프", "출범식", "선언식", "협력방안 논의",
                  "정책 진단", "정책진단", "국가 R&D", "국가R&D", "R&D사업",
                  "민·관 협력", "민관 협력",
                  # 법률·소송 (HIGH-002)
                  "특허 침해", "특허침해", "특허 소송", "특허소송", "평결",
                  "배심원", "원고", "피고", "연방지방법원",
                  # 실적·분석 (HIGH-003 + MID 다수)
                  "성과급 갈등", "성과급갈등", "후폭풍", "리그테이블", "리그 테이블",
                  "분기 영업이익", "분기 영업익", "기업가치 재평가",
                  "흑자전환", "적자전환", "흑자 전환", "적자 전환",
                  "사상 최대", "역대 최대", "거센 추격", "추격을",
                  # AX·자율제조 (HIGH-005)
                  "M.AX", "팩토리X", "FactoryX", "자율제조", "지능형 제조",
                  "지능형 자율제조", "스마트팩토리", "스마트 팩토리",
                  "디지털 트윈", "디지털트윈",
                  # 인사 (MID-023/039 등)
                  "신임 청장", "신임청장", "신임 대표", "신임대표",
                  "신임 사장", "신임사장", "신임 본부장",
                  "임명됐다", "선임됐다", "영입했다", "영입한다",
                  # 상장·M&A·금융 (MID 다수)
                  "IPO", "기업공개", "상장 신청", "상장신청",
                  "공동개발협약", "공동 개발 협약",
                  # 학술·세미나·간담회 (행사 노이즈)
                  "세미나", "심포지엄", "포럼", "간담회", "콘퍼런스", "컨퍼런스",
                  "기자간담회",
                  # 매장·점포·체험 (소규모, 영업 무관)
                  "첫 매장", "첫매장", "오프라인 매장", "오프라인매장",
                  "매장 연다", "매장연다", "본점", "1호점",
                  # 자회사·지분 인수
                  "지분 인수", "지분인수", "지분 취득", "지분취득",
                  # === '준공' 자체는 영업 골든타임 너머 ===
                  # CONSTRUCTION_ACTIONS 에서 '준공·완공' 제거했지만 본문 등장도 노이즈로
                  "본격 이전", "본격이전", "본사 이전", "본사이전",
                  # === 2026-05-27 tier 2/3 RSS 부활 후 false positive ===
                  # HR·인사 통계 (HIGH-011 삼성 퇴직률)
                  "퇴직률", "이직률", "퇴직 비율", "이직 비율",
                  # 국책 R&D 과제 (HIGH-006 현대로템 무인로봇 국책과제)
                  "국책 과제", "국책과제", "연구개발 과제", "R&D 과제",
                  "과제 수주", "과제수주", "사업자로 선정",
                  # 장애인 표준사업장 (HIGH-008 현대모비스 모아빛)
                  "표준사업장", "장애인 고용", "장애인고용",
                  # R&D 성과/논문 (HIGH-009 GIST 수소 전극 개발)
                  "전극 개발", "촉매 개발", "신소재 개발", "공동 연구",
                  "공동연구", "원천기술 개발", "원천기술개발",
                  "논문 게재", "논문게재", "학회 발표", "학회발표",
                  # 르포·정책 기사 (HIGH-012 중형트럭 보조금)
                  "르포]", "[르포", "보조금 제도", "보조금제도",
                  # 입찰정보 목록 (HIGH-010)
                  "입찰정보]", "[입찰정보", "[입찰",
                  # 인용 기관명 (본문에 "OO연구소" 인용)
                  "분석연구소", "리더스인덱스", "투자증권 연구원", "증권 연구원",
                  # 솔루션·서비스 공급사 마케팅 (HIGH false positive — 케이웨더 폭염솔루션)
                  # 주의: "관리 솔루션"(공백) / "솔루션 제공" / "통합 솔루션" 은 S-OIL AI 데이터센터,
                  #   LG ESS 미시간 등 정상 시그널을 잘못 컷 → 제외함.
                  #   "관리솔루션"(공백 X)만 — 제품·서비스 명 (마케팅 어휘)
                  "관리솔루션",
                  "솔루션 출시", "솔루션 공급사",
                  "빅데이터플랫폼", "빅데이터 플랫폼", "데이터플랫폼 기업",
                  # 산업현장 안전·보건 마케팅 (시공 무관)
                  "온열질환", "산업안전보건법",
                  "안전 솔루션", "안전솔루션", "안전 지킨다", "안전을 지킨다"]

# area 매칭 텍스트(예: "750㎡", "92㎡", "12,000㎡") → 숫자만 추출. 1,000㎡ 미만이면 무시.
_AREA_NUM_RE = re.compile(r"([\d,]+)\s*(만)?\s*㎡")


def _area_is_industrial(area_match: str) -> bool:
    """area 매칭 텍스트가 산업시설 규모(>=1,000㎡) 인지 판정.
    100~999㎡ 는 매장·사무실·체험관 등 비공사 → False.
    '만 ㎡' 단위는 무조건 True.
    """
    if not area_match:
        return False
    m = _AREA_NUM_RE.search(area_match)
    if not m:
        return False
    num_str = m.group(1).replace(",", "")
    has_man = bool(m.group(2))
    try:
        num = int(num_str)
    except ValueError:
        return False
    if has_man:  # X만 ㎡ → 산업시설 사이즈
        return True
    return num >= 1000


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def classify_rss(item: dict) -> tuple[str, str]:
    patterns = item.get("stage1_matched_patterns", []) or []
    title = item.get("title", "")
    content = item.get("content", "") or ""
    text = f"{title} {content}"

    has_strong_action = False
    has_construction_action = False
    has_weak_only_action = False
    has_done_action = False
    has_strong_target = False
    has_money = False
    has_area = False
    area_text = ""

    for p in patterns:
        if p.startswith("action:"):
            actions = p[len("action:"):].split(",")
            if any(a in STRONG_ACTIONS for a in actions):
                has_strong_action = True
            elif any(a in WEAK_ACTIONS for a in actions):
                has_weak_only_action = True
            # 건설 액션(착공/기공/신축) 별도 플래그 — 카테고리 미매칭이어도 HIGH 자격
            if any(a in CONSTRUCTION_ACTIONS for a in actions):
                has_construction_action = True
            # 시공 완료 신호(준공/완공) — 단독이면 LOW 강등 (영업 골든타임 너머)
            if any(a in DONE_ACTIONS for a in actions):
                has_done_action = True
        elif p.startswith("target:"):
            targets = p[len("target:"):].split(",")
            if any(t in STRONG_TARGETS for t in targets):
                has_strong_target = True
            # CR 두글자 단독 → 본문에 '클린룸/반도체/팹/FAB' 같이 안 나오면 무시.
            if "CR" in targets and not has_strong_target:
                if any(k in text for k in ("클린룸", "반도체", "FAB", "팹")):
                    has_strong_target = True
            # "공장" target 도 마찬가지 — STRONG_TARGETS 에 이미 포함됨 (재확인)
        elif p.startswith("money:"):
            has_money = True
        elif p.startswith("area:"):
            area_text = p[len("area:"):]
            # area 가 1,000㎡ 미만이면 산업시설 아님 → 무시
            if _area_is_industrial(area_text):
                has_area = True

    noise_hits = [k for k in NOISE_KEYWORDS if k in text]
    is_noisy = len(noise_hits) >= 2 or any(k in title for k in NOISE_KEYWORDS)

    # 본문 깊숙히 명백한 미래 시공 시그널이 있으면 노이즈 무시 — over-cut 보정.
    # 예: 셀트리온 "AX 투트랙" 기사 — 제목엔 'AI 도입' 노이즈지만 본문에
    # "송도에 신규 건설 예정인 원료의약품 4·5공장" 명시 → 영업 가치 있음.
    EXPLICIT_FUTURE_CONSTRUCTION = (
        "신규 건설", "신축 예정", "착공 예정", "기공 예정",
        "신축 계획", "건립 예정", "신규로 건설", "신축할",
        "신규 공장", "제2공장 신축", "제3공장 신축", "제4공장 신축", "제5공장 신축",
        "신규 공장 건설", "신규 생산시설", "신규 생산 시설",
    )
    has_explicit_future = any(k in text for k in EXPLICIT_FUTURE_CONSTRUCTION)

    # 카테고리 '기타' = 산업키워드 0개. HIGH 자격 박탈 (산업 매칭 없으면 무조건 강등).
    cats = _news_cats(item)
    has_industry_category = bool(cats) and cats != ["기타"]

    if has_strong_action and has_strong_target and (has_money or has_area) and not is_noisy and has_industry_category:
        return "HIGH", "강한 행동+대상+규모 매칭"
    if has_strong_action and has_strong_target and not is_noisy and has_industry_category:
        return "HIGH", "강한 행동+대상 매칭"
    # 명백한 건설 액션(착공/기공/신축) + 강한 target → 카테고리 미매칭이어도 HIGH.
    # 이유: "파마리서치 강릉 제5공장 착공" 처럼 본문 짧아 categorize 가 제약/바이오 못 잡는
    # 명백한 시공 케이스를 살리기 위함. `신설`/`증설`(조직신설·라인증설 노이즈)은 제외.
    if has_construction_action and has_strong_target and not is_noisy:
        return "HIGH", "건설 행동+공장 매칭 (카테고리 미매칭 허용)"
    # 본문에 명백한 미래 시공 시그널 ("신규 건설", "신축 예정") + 강한 target → 노이즈 무시 HIGH.
    # 셀트리온 송도 원료의약품 4·5공장 같이 제목엔 AI/AX 노이즈지만 본문에 명백한 신축 발표.
    if has_explicit_future and has_strong_target and has_strong_action:
        return "HIGH", "본문에 명백한 미래 시공 시그널 (노이즈 무시)"
    # 준공/완공만 — 시공 완료, 영업 무관. 자이씨앤에이는 시공사라 다 지어진 공장은 가치 X.
    # 단, 같은 기사에 STRONG_ACTIONS(착공/신축 등) 미래 시그널이 함께 있으면 위 HIGH 룰에서 잡힘.
    if has_done_action and not (has_strong_action or has_construction_action or has_explicit_future):
        return "LOW", "준공/완공 — 시공 완료, 영업 무관"
    # ── 노이즈 키워드 컷은 HIGH 직후 (regression 방지) ──
    if is_noisy:
        return "LOW", f"노이즈 키워드: {', '.join(noise_hits[:3])}"
    # 산업 카테고리 없으면 강제 MID — 산업키워드 0개는 영업 우선순위 아님
    if has_strong_action and has_strong_target and not has_industry_category:
        return "MID", "강한 행동+대상 (산업 카테고리 미매칭)"
    if has_strong_action and (has_money or has_area):
        return "MID", "강한 행동+규모 (대상 약함)"
    if has_strong_target and (has_money or has_area):
        return "MID", "강한 대상+규모 (행동 약함)"
    if has_weak_only_action and not has_strong_target:
        return "LOW", "약한 행동만, 대상 불명확"
    return "MID", "기타"


def _esc(s: str) -> str:
    return html.escape(s or "")


def _preview(text: str, n: int = 220) -> str:
    if not text:
        return ""
    s = text.replace("\n", " ").replace("\r", " ").strip()
    return s[:n] + ("…" if len(s) > n else "")


def _money_in_title(s: str) -> str:
    m = re.search(r"\d[\d,]*\s*(?:백만원|억원|조원|만원|원)", s or "")
    return m.group(0) if m else ""


# === Stage1 매칭 패턴 → 한국어 라벨 ===
# stage1_matched_patterns 에는 "action:신설", "target:공장", "money:100억" 형식으로 저장됨.
# 영업팀이 보기엔 영문 키가 직관적이지 않아 한국어로 변환.
_KW_LABELS = {
    "action": "행동",
    "target": "대상",
    "money": "금액",
    "area": "면적",
    "exclude": "제외",
}
# 라벨별 색상 — exclude(부정 신호) 만 빨강, 나머지는 녹색(긍정 신호)
_KW_LABEL_COLORS = {
    "action": "var(--accent-good)",
    "target": "var(--accent-good)",
    "money": "var(--accent-good)",
    "area": "var(--accent-good)",
    "exclude": "var(--accent-bad)",
}
_KW_VALUE_COLORS = {
    "exclude": "var(--accent-bad-soft)",  # 부정 신호 값도 살짝 빨강 톤
}


def _format_match_patterns(patterns, limit: int = 4) -> str:
    """stage1_matched_patterns → 한국어 라벨 HTML.
    ['action:신설,라인', 'target:공장'] → '<b>행동</b> 신설, 라인 · <b>대상</b> 공장'
    exclude(제외) 키워드는 빨강으로 강조.
    """
    if not patterns:
        return ""
    parts = []
    for p in list(patterns)[:limit]:
        if ":" in p:
            kind, val = p.split(":", 1)
            label = _KW_LABELS.get(kind, kind)
            label_color = _KW_LABEL_COLORS.get(kind, "var(--accent-good)")
            val_color = _KW_VALUE_COLORS.get(kind, "var(--text-soft)")
            parts.append(
                f'<b style="color:{label_color};font-weight:600;">{label}</b> '
                f'<span style="color:{val_color};">{_esc(val)}</span>'
            )
        else:
            parts.append(_esc(p))
    return ' <span style="color:var(--muted);">·</span> '.join(parts)


def _is_primary_signal(it: dict) -> bool:
    t = (it.get("title") or "").replace("ㆍ", "").replace("·", "").replace(" ", "")
    return "공급계약" not in t


# === DART 1차 — 건설 영업 대상 여부 판단 ===
# 신규시설투자등 공시는 부동산/시설물뿐 아니라 동산 자산 취득(선박 건조, 항공기/엔진 구매,
# 차량 구매 등) 도 같이 들어옴. 시공 영업 대상이 아니므로 컷.
# 화이트리스트(건설 키워드) 매치 우선 — 매치되면 무조건 통과.
# 화이트리스트 미매치 + 블랙리스트(동산 키워드) 매치 → 컷.
# 둘 다 미매치 → 통과 (보수적, 알 수 없음).
_CONSTRUCTION_KW = (
    "공장", "플랜트", "생산시설", "생산설비", "생산라인", "라인증설", "사옥",
    "연구소", "물류센터", "센터건립", "부지", "신축", "증축", "증설", "건물",
    "건립", "건설", "본사", "기숙사", "사업장", "산업단지", "설비증설",
    "공사", "리모델링", "복합단지", "데이터센터",
)
_NON_CONSTRUCTION_KW = (
    "VLCC", "LNG선", "컨테이너선", "탱커선", "벌크선", "선박건조",
    "선대확대", "선박매입", "선박인수", "X척건조",  # X척 건조 은 정규식으로
    "예비엔진", "항공기구매", "항공기매입", "항공기도입", "기재도입",
    "엔진구매", "엔진매입", "엔진도입", "차량매입", "차량구매",
)
_SHIP_BUILD_RE = re.compile(r"\d+척\s*건조")


# === EAIS 건축인허가 본문 정형필드 파서 ===
# content 패턴: "주소: X | 주용도: Y | 건축구분: Z | 인허가일: YYYYMMDD |
#               연면적: A㎡ | 건축면적: B㎡ | 대지면적: C㎡ | 주건축물수: D동 |
#               착공예정: | 실제착공: | 사용승인: | 건물명: E"
_EAIS_ADDR_RE = re.compile(r"주소:\s*([^|]+?)\s*\|")
_EAIS_USAGE_RE = re.compile(r"주용도:\s*([^|]+?)\s*\|")
_EAIS_KIND_RE = re.compile(r"건축구분:\s*([^|]+?)\s*\|")
_EAIS_DATE_RE = re.compile(r"인허가일:\s*(\d{8})")
_EAIS_GROSS_AREA_RE = re.compile(r"연면적:\s*([\d,]+(?:\.\d+)?)\s*㎡")
_EAIS_BLDG_AREA_RE = re.compile(r"건축면적:\s*([\d,]+(?:\.\d+)?)\s*㎡")
_EAIS_LAND_AREA_RE = re.compile(r"대지면적:\s*([\d,]+(?:\.\d+)?)\s*㎡")
_EAIS_BLDG_COUNT_RE = re.compile(r"주건축물수:\s*(\d+)\s*동")
_EAIS_ATCH_BLDG_RE = re.compile(r"부속건축물수:\s*(\d+)\s*동")
_EAIS_JIYUK_RE = re.compile(r"지역지구:\s*([^|]+?)\s*\|")
_EAIS_JIMOK_RE = re.compile(r"지목:\s*([^|]+?)\s*\|")
_EAIS_GUYUK_RE = re.compile(r"구역:\s*([^|]+?)\s*\|")
_EAIS_BC_RAT_RE = re.compile(r"건폐율:\s*([\d.]+)\s*%")
_EAIS_VL_RAT_RE = re.compile(r"용적률:\s*([\d.]+)\s*%")
_EAIS_PKNG_RE = re.compile(r"주차장:\s*(\d+)\s*대")
_EAIS_HHLD_RE = re.compile(r"세대수:\s*(\d+)\s*세대")
_EAIS_STCNS_SCHED_RE = re.compile(r"착공예정:\s*(\d{8})")
_EAIS_REAL_STCNS_RE = re.compile(r"실제착공:\s*(\d{8})")
_EAIS_USE_APR_RE = re.compile(r"사용승인:\s*(\d{8})")
_EAIS_BLDG_NAME_RE = re.compile(r"건물명:\s*([^|]+?)(?:\s*\||\s*$)")
_EAIS_COST_RE = re.compile(r"추정공사비:\s*([^|]+?)\s*\(카테고리:")
EAIS_LARGE_AREA_THRESHOLD = 10000.0  # 10,000㎡ — 대형 시설 강조
EAIS_LARGE_COST_MAN = 5_000_000     # 500억 만원 — 대형 사업 강조 (G2B/DART 와 통일)


def _parse_eais_cost_man(label: str) -> int:
    """eais.py format_cost 가 만든 라벨 → 만원 단위 정수.
    '480억 5,001' → 4,805,001 · '4,500억' → 45,000,000 · '5,000만' → 5,000 · '추정불가' → 0.
    """
    raw = (label or "").strip()
    if not raw or raw == "추정불가":
        return 0
    m = re.match(r"([\d,]+)억(?:\s+([\d,]+))?$", raw)
    if m:
        eok = int(m.group(1).replace(",", ""))
        man = int(m.group(2).replace(",", "")) if m.group(2) else 0
        return eok * 10000 + man
    m = re.match(r"([\d,]+)만$", raw)
    if m:
        return int(m.group(1).replace(",", ""))
    return 0


def _extract_eais_fields(content: str) -> dict:
    """EAIS content 에서 정형필드 추출. 못 찾으면 빈 문자열/0."""
    result = {
        "addr": "",
        "usage": "",
        "kind": "",
        "permit_date": "",
        "gross_area": 0.0,
        "bldg_area": 0.0,
        "land_area": 0.0,
        "bldg_count": 0,
        "bldg_name": "",
        # 추가 영업/시공 정보 (eais.py 가 content 에 append 함)
        "atch_bldg_count": 0,
        "jiyuk": "",
        "bc_rat": 0.0,
        "vl_rat": 0.0,
        "pkng_count": 0,
        "hhld_count": 0,
        "cost_label": "",
        "cost_man": 0,
        "jimok": "",
        "guyuk": "",
        "stcns_sched": "",     # YYYY-MM-DD
        "real_stcns": "",      # YYYY-MM-DD
        "use_apr": "",         # YYYY-MM-DD
    }
    if not content:
        return result
    m = _EAIS_ADDR_RE.search(content)
    if m:
        result["addr"] = m.group(1).strip()
    m = _EAIS_USAGE_RE.search(content)
    if m:
        result["usage"] = m.group(1).strip()
    m = _EAIS_KIND_RE.search(content)
    if m:
        result["kind"] = m.group(1).strip()
    m = _EAIS_DATE_RE.search(content)
    if m:
        d = m.group(1)
        result["permit_date"] = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    for key, regex in (("gross_area", _EAIS_GROSS_AREA_RE),
                       ("bldg_area", _EAIS_BLDG_AREA_RE),
                       ("land_area", _EAIS_LAND_AREA_RE)):
        m = regex.search(content)
        if m:
            try:
                result[key] = float(m.group(1).replace(",", ""))
            except ValueError:
                pass
    m = _EAIS_BLDG_COUNT_RE.search(content)
    if m:
        try:
            result["bldg_count"] = int(m.group(1))
        except ValueError:
            pass
    m = _EAIS_ATCH_BLDG_RE.search(content)
    if m:
        try:
            result["atch_bldg_count"] = int(m.group(1))
        except ValueError:
            pass
    m = _EAIS_JIYUK_RE.search(content)
    if m:
        result["jiyuk"] = m.group(1).strip()
    m = _EAIS_BC_RAT_RE.search(content)
    if m:
        try:
            result["bc_rat"] = float(m.group(1))
        except ValueError:
            pass
    m = _EAIS_VL_RAT_RE.search(content)
    if m:
        try:
            result["vl_rat"] = float(m.group(1))
        except ValueError:
            pass
    m = _EAIS_PKNG_RE.search(content)
    if m:
        try:
            result["pkng_count"] = int(m.group(1))
        except ValueError:
            pass
    m = _EAIS_HHLD_RE.search(content)
    if m:
        try:
            result["hhld_count"] = int(m.group(1))
        except ValueError:
            pass
    m = _EAIS_BLDG_NAME_RE.search(content)
    if m:
        result["bldg_name"] = m.group(1).strip()
    m = _EAIS_COST_RE.search(content)
    if m:
        result["cost_label"] = m.group(1).strip()
        result["cost_man"] = _parse_eais_cost_man(result["cost_label"])
    m = _EAIS_JIMOK_RE.search(content)
    if m:
        result["jimok"] = m.group(1).strip()
    m = _EAIS_GUYUK_RE.search(content)
    if m:
        result["guyuk"] = m.group(1).strip()
    for key, regex in (("stcns_sched", _EAIS_STCNS_SCHED_RE),
                       ("real_stcns", _EAIS_REAL_STCNS_RE),
                       ("use_apr", _EAIS_USE_APR_RE)):
        m = regex.search(content)
        if m:
            d = m.group(1)
            result[key] = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return result


def _format_area(sqm: float) -> str:
    """면적 ㎡ → '1.23만㎡' (모든 값을 만㎡ 단위로 통일. 3,000㎡ → 0.30만㎡)."""
    if sqm <= 0:
        return "—"
    return f"{sqm / 10000:.2f}만㎡"


# === G2B 입찰공고 본문 정형필드 파서 ===
# content 패턴: "공고기관: X | 수요기관: Y | 추정가격: Z원 | 개찰일시: YYYY-MM-DD HH:MM:SS"
_G2B_NOTICE_RE = re.compile(r"공고기관:\s*([^|]+?)\s*\|")
_G2B_DEMAND_RE = re.compile(r"수요기관:\s*([^|]+?)\s*\|")
_G2B_PRICE_RE = re.compile(r"추정가격:\s*([\d,]+)\s*원")
_G2B_OPEN_RE = re.compile(r"개찰일시:\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}(?::\d{2})?)")
G2B_PRICE_THRESHOLD = 50_000_000_000  # 500억원 — 영업 단가 기준선 (강조)
G2B_MIN_PRICE = 100_000_000  # 1억원 — 이 미만은 소액 / 추정가 미공개 → 컷


def _extract_g2b_fields(content: str) -> dict:
    """G2B 입찰공고 content 에서 공고기관/수요기관/추정가격/개찰일시 추출."""
    result = {
        "notice": "",
        "demand": "",
        "price": 0,
        "open_date": "",
        "open_time": "",
    }
    if not content:
        return result
    m = _G2B_NOTICE_RE.search(content)
    if m:
        result["notice"] = m.group(1).strip()
    m = _G2B_DEMAND_RE.search(content)
    if m:
        result["demand"] = m.group(1).strip()
    m = _G2B_PRICE_RE.search(content)
    if m:
        try:
            result["price"] = int(m.group(1).replace(",", ""))
        except ValueError:
            pass
    m = _G2B_OPEN_RE.search(content)
    if m:
        result["open_date"] = m.group(1)
        result["open_time"] = m.group(2)
    return result


def _is_dart_correction(it: dict) -> bool:
    """DART 공시가 [기재정정] 인지."""
    return "[기재정정]" in (it.get("title") or "")


def _is_dart_asset_acquisition(it: dict) -> bool:
    """DART 공시가 '유형자산 취득결정' 또는 '유형자산 양수결정' 인지.
    title 의 공백 제거 후 패턴 매칭. '신규시설투자등'과 본문 구조가 다름.
    """
    t = (it.get("title") or "").replace(" ", "")
    return ("유형자산취득결정" in t) or ("유형자산양수결정" in t)


def _dart_invest_amount(it: dict) -> int:
    """DART 1차 신규시설투자등 정렬키 — 본문 투자금액(원)."""
    return _extract_dart_invest_fields(it.get("content") or "").get("amount", 0)


def _dart_asset_amount(it: dict) -> int:
    """DART 1차 유형자산취득결정 정렬키 — 본문 취득금액(원)."""
    return _extract_dart_asset_fields(it.get("content") or "").get("amount", 0)


# === DART 유형자산 취득결정 본문 정형필드 파서 ===
# 다양한 본문 패턴:
#   A: "1. 취득목적물 X      2. 취득내역 취득금액(원) N ..."
#   B: "1. 취득물건 구분 X - 취득물건명 Y  2. 취득내역 ..."
#   C: "1. 정정관련 공시서류 ... 정정사항 정정항목 정정전 정정후 ..." (본 신고서 부재 케이스)
#   D: "1. 제출사유 X 2. 주요내용 ..." (양수결정 철회 케이스)
# 거래상대 라벨 변형: "거래상대" / "거래상대방"
_DART_ASSET_BODY_RE = re.compile(
    r"1\.\s*취득(?:목적물|물건\s*구분|물건)\s+(.+)", re.DOTALL,
)
_DART_ASSET_TARGET_RE = re.compile(
    r"1\.\s*취득(?:목적물|물건\s*구분|물건)\s+(.+?)\s+2\.\s*취득내역", re.DOTALL,
)
_DART_ASSET_TARGET_NAME_RE = re.compile(
    r"취득물건명\s+(.+?)(?:\s+2\.\s*취득내역|\s+-\s*취득)", re.DOTALL,
)
_DART_ASSET_AMOUNT_RE = re.compile(r"취득금액\s*\(원\)\s*([\d,]+)")
_DART_ASSET_AMOUNT_FALLBACK_RE = re.compile(r"취득가액\s*\(원\)\s*([\d,]+)")
_DART_ASSET_RATIO_RE = re.compile(r"(?:연결)?자산총액대비\s*\(?%\)?\s*([\d.]+)")
_DART_ASSET_PARTNER_RE = re.compile(
    r"3\.\s*거래상대(?:방)?\s+(.+?)(?:\s+-?\s*회사와의\s*관계|\s+-\s*최근\s*매출액|\s+4\.\s*취득목적)",
    re.DOTALL,
)
_DART_ASSET_RELATION_RE = re.compile(
    r"-?\s*회사와의\s*관계\s+([^|]+?)(?:\s+4\.\s*취득목적|\s+회사와\s+최근|$)",
    re.DOTALL,
)
_DART_ASSET_PURPOSE_RE = re.compile(
    r"4\.\s*취득목적\s+(.+?)\s+5\.\s*취득예정일자", re.DOTALL,
)
_DART_ASSET_DATE_RE = re.compile(r"5?\.?\s*취득예정일자\s*(\d{4}-\d{2}-\d{2})")
# 양수결정 철회 본문에서 양수금액·계약상대방 fallback
_DART_ASSET_WITHDRAW_AMOUNT_RE = re.compile(r"양수금액\s*[:：]?\s*([\d,]+)\s*원?")
_DART_ASSET_WITHDRAW_PARTNER_RE = re.compile(r"계약상대방\s*[:：]?\s*([^\n]+?)(?:\s+라\.|\s+\d\.|\s+양수금액|$)")


def _extract_dart_asset_fields(content: str) -> dict:
    """DART 유형자산 취득결정 본문 정형 필드 추출.

    파싱 순서:
    1. 본 신고서 영역 (마지막 "1. 취득(목적물|물건 구분|물건)" 매치 이후) 우선
    2. 매치 실패 시 정정사항만 있는 케이스 → 본문 전체에서 부분 정보 fallback
    3. 양수결정 철회 케이스 → 별도 패턴 (양수금액·계약상대방)
    """
    result = {
        "target": "",          # 취득목적물/취득물건 구분 (토지 및 건물 등)
        "target_name": "",     # 취득물건명 (상세 위치/주소)
        "amount": 0,           # 취득금액(원)
        "asset_ratio": "",     # 자산총액대비(%)
        "partner": "",         # 거래상대(방) — 매도자
        "relation": "",        # 회사와의 관계
        "purpose": "",         # 취득목적 (R&D센터 / 신사옥 / 공장부지 등)
        "expected_date": "",   # 취득예정일자
        "is_withdrawn": False, # 양수결정 철회 여부
    }
    if not content:
        return result

    # 양수결정 철회 케이스 (제출사유 = 철회) — title 에선 못 잡았더라도 본문에서 판별
    if "양수결정 철회" in content or "유형자산양수결정) 철회" in content:
        result["is_withdrawn"] = True

    matches = list(_DART_ASSET_BODY_RE.finditer(content))
    if matches:
        # === 패턴 A/B: 본 신고서 영역 ===
        body = content[matches[-1].start():]
        m = _DART_ASSET_TARGET_RE.search(body)
        if m:
            result["target"] = m.group(1).strip()
            # "토지 및 건물 - 취득물건명 X" 형태에서 분리
            if " - 취득물건명 " in result["target"]:
                head, _, tail = result["target"].partition(" - 취득물건명 ")
                result["target"] = head.strip()
                result["target_name"] = tail.strip()
        m = _DART_ASSET_TARGET_NAME_RE.search(body)
        if m and not result["target_name"]:
            result["target_name"] = m.group(1).strip()
        m = _DART_ASSET_AMOUNT_RE.search(body) or _DART_ASSET_AMOUNT_FALLBACK_RE.search(body)
        if m:
            try:
                result["amount"] = int(m.group(1).replace(",", ""))
            except ValueError:
                pass
        m = _DART_ASSET_RATIO_RE.search(body)
        if m:
            result["asset_ratio"] = m.group(1)
        m = _DART_ASSET_PARTNER_RE.search(body)
        if m:
            partner = m.group(1).strip().rstrip("-").strip()
            if partner and partner not in ("-", "—"):
                result["partner"] = partner
        m = _DART_ASSET_RELATION_RE.search(body)
        if m:
            rel = m.group(1).strip()
            if (rel and rel.replace("-", "").replace("—", "").strip()
                    and rel not in ("없음", "해당없음", "미해당")):
                result["relation"] = rel
        m = _DART_ASSET_PURPOSE_RE.search(body)
        if m:
            result["purpose"] = m.group(1).strip()
        m = _DART_ASSET_DATE_RE.search(body)
        if m:
            result["expected_date"] = m.group(1)
    else:
        # === 패턴 C: 정정사항만 있는 케이스 — 본문 전체에서 정정후 값 fallback ===
        # "취득가액(원) 정정전 정정후" → 두 번 매치 중 후자 사용
        amount_matches = (list(_DART_ASSET_AMOUNT_RE.finditer(content))
                          + list(_DART_ASSET_AMOUNT_FALLBACK_RE.finditer(content)))
        amounts = []
        for m in amount_matches:
            try:
                amounts.append(int(m.group(1).replace(",", "")))
            except ValueError:
                pass
        if amounts:
            result["amount"] = max(amounts)  # 정정후 값이 보통 가장 큼 (또는 동일)
        ratio_matches = list(_DART_ASSET_RATIO_RE.finditer(content))
        if ratio_matches:
            result["asset_ratio"] = ratio_matches[-1].group(1)
        date_matches = list(_DART_ASSET_DATE_RE.finditer(content))
        if date_matches:
            result["expected_date"] = date_matches[-1].group(1)

        # === 패턴 D: 양수결정 철회 fallback ===
        if result["is_withdrawn"]:
            m = _DART_ASSET_WITHDRAW_AMOUNT_RE.search(content)
            if m and result["amount"] == 0:
                try:
                    result["amount"] = int(m.group(1).replace(",", ""))
                except ValueError:
                    pass
            m = _DART_ASSET_WITHDRAW_PARTNER_RE.search(content)
            if m:
                partner = m.group(1).strip().rstrip("-").strip()
                if partner and partner not in ("-", "—"):
                    result["partner"] = partner

    return result


def _is_dart_withdrawn(it: dict) -> bool:
    """DART 1차 공시가 '시설투자/자산취득 철회' 케이스인지 — 영업 노이즈.

    판별 우선순위:
    1. title 에 명시적 "철회" 또는 "(철회)" 패턴
    2. 본문 파싱 결과의 is_withdrawn 플래그 (정정사유에 "철회" 포함 등)
    """
    title = it.get("title") or ""
    if "철회" in title:
        return True
    content = it.get("content") or ""
    if _extract_dart_invest_fields(content).get("is_withdrawn"):
        return True
    if _extract_dart_asset_fields(content).get("is_withdrawn"):
        return True
    return False


def _is_construction_relevant(it: dict) -> bool:
    """DART 신규시설투자등 항목이 건설 영업 대상인지 판단.

    invest_target + purpose 텍스트에 건설 키워드 매치되면 통과,
    동산 키워드만 매치되면 컷.
    """
    fields = _extract_dart_invest_fields(it.get("content") or "")
    text = f"{fields['invest_target']} {fields['purpose']}"
    text_compact = text.replace(" ", "")
    # 1) 화이트리스트 우선
    if any(kw in text_compact for kw in _CONSTRUCTION_KW):
        return True
    # 2) 블랙리스트
    if any(kw in text_compact for kw in _NON_CONSTRUCTION_KW):
        return False
    if _SHIP_BUILD_RE.search(text):
        return False
    # 3) 알 수 없음 → 통과 (보수적)
    return True


# === DART 공급계약 계약금액 추출 ===
# content 자유텍스트 안에 "계약금액 총액(원) 117,002,465,491" 같이 들어있음.
# 정정공시는 정정전/정정후 둘 다 매칭됨 → 최대값 사용 (보수적으로 통과시킴).
_DART_AMOUNT_RE = re.compile(r"계약금액\s*총액\s*\(원\)\s*([\d,]+)")
_DART_AMOUNT_FALLBACK_RE = re.compile(r"확정\s*계약금액\s*([\d,]+)")
DART_CONTRACT_THRESHOLD = 50_000_000_000  # 500억원


def _extract_dart_contract_amount(it: dict) -> int:
    """DART 공급계약체결 content 에서 계약금액(원) 추출. 못 찾으면 0."""
    content = it.get("content") or ""
    if not content:
        return 0
    amounts: list[int] = []
    for m in _DART_AMOUNT_RE.finditer(content):
        try:
            amounts.append(int(m.group(1).replace(",", "")))
        except ValueError:
            pass
    if not amounts:
        for m in _DART_AMOUNT_FALLBACK_RE.finditer(content):
            try:
                amounts.append(int(m.group(1).replace(",", "")))
            except ValueError:
                pass
    return max(amounts) if amounts else 0


# === DART 단일판매ㆍ공급계약체결 본문 정형필드 파서 ===
# content 패턴:
#   "1. 판매ㆍ공급계약 내용 X 2. 계약내역 ... 계약금액 총액(원) N ...
#    매출액 대비(%) M  3. 계약상대방 P - 최근 매출액(원) - 주요사업 - 회사와의 관계 R
#    4. 판매ㆍ공급지역 A  5. 계약기간 시작일 YYYY-MM-DD 종료일 YYYY-MM-DD ..."
# 변형: "1. 판매ㆍ공급계약 구분 공사수주 - 체결계약명 P5 Project 2. 계약내역 ..."
_DART2_BODY_RE = re.compile(r"1\.\s*판매ㆍ?공급계약\s*(?:내용|구분)\s+(.+)", re.DOTALL)
_DART2_CONTENT_RE = re.compile(r"1\.\s*판매ㆍ?공급계약\s*내용\s+(.+?)\s+2\.\s*계약내역", re.DOTALL)
_DART2_TYPE_AND_NAME_RE = re.compile(
    r"1\.\s*판매ㆍ?공급계약\s*구분\s+([^\n\-]+?)\s+-?\s*체결계약명\s+(.+?)\s+2\.\s*계약내역",
    re.DOTALL,
)
_DART2_PARTNER_RE = re.compile(
    r"3\.\s*계약상대(?:방)?\s+(.+?)(?:\s+-\s+최근\s*매출액|\s+-\s+회사와의\s*관계|\s+4\.\s*판매)",
    re.DOTALL,
)
_DART2_RELATION_RE = re.compile(
    r"회사와의\s*관계\s+([^|]+?)(?:\s+회사와\s+최근|\s+4\.\s*판매|$)",
    re.DOTALL,
)
_DART2_AREA_RE = re.compile(r"4\.\s*판매ㆍ?공급지역\s+(.+?)\s+5\.\s*계약기간", re.DOTALL)
_DART2_PERIOD_RE = re.compile(
    r"5\.\s*계약기간\s+시작일\s*(\d{4}-\d{2}-\d{2})\s+종료일\s*(\d{4}-\d{2}-\d{2})"
)
_DART2_REV_PCT_RE = re.compile(r"매출액\s*대비\s*\(?%\)?\s*([\d.]+)")


def _extract_dart_contract_fields(content: str) -> dict:
    """DART 단일판매ㆍ공급계약체결 본문 정형 필드 추출.

    정정공시는 본 신고서가 뒤쪽에 있으므로 마지막 "1. 판매ㆍ공급계약" 매치 이후 영역만 사용.
    못 찾는 필드는 빈 문자열.
    """
    result = {
        "contract_kind": "",   # 계약 구분 (공사수주 / 기계제작 등)
        "contract_name": "",   # 체결계약명 또는 계약내용
        "partner": "",         # 계약상대(방)
        "relation": "",        # 회사와의 관계 (계열회사 등)
        "area": "",            # 판매ㆍ공급지역
        "period_from": "",
        "period_to": "",
        "rev_pct": "",         # 매출액 대비(%)
    }
    if not content:
        return result
    matches = list(_DART2_BODY_RE.finditer(content))
    if not matches:
        return result
    body = content[matches[-1].start():]  # 마지막 본 신고서 이후

    # 패턴 A: 내용 단독
    m = _DART2_CONTENT_RE.search(body)
    if m:
        result["contract_name"] = m.group(1).strip()
    # 패턴 B: 구분 + 체결계약명 (있으면 덮어씀, 더 구체적)
    m = _DART2_TYPE_AND_NAME_RE.search(body)
    if m:
        result["contract_kind"] = m.group(1).strip()
        result["contract_name"] = m.group(2).strip()

    m = _DART2_PARTNER_RE.search(body)
    if m:
        partner = m.group(1).strip().rstrip("-").strip()
        if partner and partner not in ("-", "—"):
            result["partner"] = partner
    m = _DART2_RELATION_RE.search(body)
    if m:
        rel = m.group(1).strip()
        # dash/space 만 있는 빈 관계는 제외 ("-", "- -", "— —" 등)
        if rel and rel.replace("-", "").replace("—", "").strip():
            result["relation"] = rel
    m = _DART2_AREA_RE.search(body)
    if m:
        result["area"] = m.group(1).strip()
    m = _DART2_PERIOD_RE.search(body)
    if m:
        result["period_from"] = m.group(1)
        result["period_to"] = m.group(2)
    m = _DART2_REV_PCT_RE.search(body)
    if m:
        result["rev_pct"] = m.group(1)
    return result


# === DART 신규시설투자등 본문 정형필드 파서 ===
# 공시 본문 패턴:
#   "1. 투자구분 <type> 2. 투자내역 투자금액(원) <amount> 자기자본(원) <eq> 자기자본대비(%) <r>
#    대규모법인여부 X 3. 투자목적 <purpose> 4. 투자기간 시작일 YYYY-MM-DD 종료일 YYYY-MM-DD ..."
# 정정공시는 앞쪽에 "정정사항 정정항목 정정전 정정후 ..." 가 붙고 뒤쪽에 동일 패턴의 본 신고서가 옴.
# → 가장 뒤쪽 "1. 투자구분" 매치(=본 신고서)를 우선 사용.
_DART_INVEST_BODY_RE = re.compile(r"1\.\s*투자구분\s+(.+)", re.DOTALL)
_DART_INVEST_TYPE_RE = re.compile(r"^(.+?)\s+2\.\s*투자내역", re.DOTALL)
_DART_INVEST_AMOUNT_RE = re.compile(r"투자금액\s*\(원\)\s*([\d,]+)")
_DART_EQUITY_RATIO_RE = re.compile(r"자기자본대비\s*\(%\)\s*([\d.]+)")
_DART_PURPOSE_RE = re.compile(r"3\.\s*투자목적\s+(.+?)\s+4\.\s*투자기간", re.DOTALL)
_DART_PERIOD_RE = re.compile(r"투자기간\s+시작일\s*(\d{4}-\d{2}-\d{2})\s+종료일\s*(\d{4}-\d{2}-\d{2})")
# 정정공시 정정사유 — 본 신고서 없거나 철회 케이스 fallback 으로 사용
_DART_INVEST_CORR_REASON_RE = re.compile(
    r"3\.\s*정정사유\s+(.+?)\s+4\.\s*정정사항", re.DOTALL,
)


def _extract_dart_invest_fields(content: str) -> dict:
    """DART 신규시설투자등 content 에서 투자구분/금액/자본대비/목적/기간 정형필드 추출.

    파싱 순서:
    1. 본 신고서 영역 (마지막 "1. 투자구분" 이후) 우선
    2. 본문에 "철회" 키워드 또는 정정사유에 "철회" → is_withdrawn = True
       (HLB바이오스텝/세종메디칼 같은 시설투자 계획 자체 철회)
    3. 본 신고서 없거나 모든 값이 비어있는 경우 → 정정사항에서 정정전 값 fallback
       (참고용. 영업 가치는 없지만 원래 계획 규모는 보여줌)

    "1. 투자구분 신규시설투자 - 투자대상 광주 1공장 ..." 같이 sub-label 이 붙은 경우
    invest_type 은 "신규시설투자", invest_target = "광주 1공장 ..." 로 분리.
    """
    result = {
        "invest_type": "",
        "invest_target": "",
        "amount": 0,
        "equity_ratio": "",
        "purpose": "",
        "period_from": "",
        "period_to": "",
        "is_withdrawn": False,         # 시설투자 계획 철회 여부
        "correction_reason": "",       # 정정사유 (fallback)
    }
    if not content:
        return result

    # 정정사유 우선 추출 — 철회 판별 + fallback purpose 로 사용
    m = _DART_INVEST_CORR_REASON_RE.search(content)
    if m:
        reason = m.group(1).strip()
        result["correction_reason"] = reason
        if "철회" in reason:
            result["is_withdrawn"] = True
    # 본문 전체에 "철회" 키워드가 있어도 보수적으로 철회 추정 (정정사유 외 별도 진술)
    if not result["is_withdrawn"] and "신규시설투자 철회" in content:
        result["is_withdrawn"] = True

    # 본 신고서 영역 추출
    matches = list(_DART_INVEST_BODY_RE.finditer(content))
    if matches:
        body = matches[-1].group(1)
        m = _DART_INVEST_TYPE_RE.search(body)
        if m:
            raw_type = m.group(1).strip()
            if " - " in raw_type:
                head, tail = raw_type.split(" - ", 1)
                result["invest_type"] = head.strip()
                tail = tail.strip()
                for prefix in ("투자대상 ", "투자내용 ", "투자내역 "):
                    if tail.startswith(prefix):
                        tail = tail[len(prefix):].strip()
                        break
                result["invest_target"] = tail
            else:
                result["invest_type"] = raw_type
        m = _DART_INVEST_AMOUNT_RE.search(body)
        if m:
            try:
                result["amount"] = int(m.group(1).replace(",", ""))
            except ValueError:
                pass
        m = _DART_EQUITY_RATIO_RE.search(body)
        if m:
            result["equity_ratio"] = m.group(1)
        m = _DART_PURPOSE_RE.search(body)
        if m:
            result["purpose"] = m.group(1).strip()
        m = _DART_PERIOD_RE.search(body)
        if m:
            result["period_from"] = m.group(1)
            result["period_to"] = m.group(2)

    # === Fallback: 본 신고서 없음 또는 본 신고서에 amount 가 0 (정정후 dash) ===
    # 정정사항 영역에서 정정전 값을 추출 (원래 계획 규모, 참고용)
    if result["amount"] == 0:
        all_amounts: list[int] = []
        for m in _DART_INVEST_AMOUNT_RE.finditer(content):
            try:
                all_amounts.append(int(m.group(1).replace(",", "")))
            except ValueError:
                pass
        if all_amounts:
            result["amount"] = max(all_amounts)
    if not result["equity_ratio"]:
        ratio_matches = list(_DART_EQUITY_RATIO_RE.finditer(content))
        if ratio_matches:
            result["equity_ratio"] = ratio_matches[0].group(1)  # 정정전 = 원래 비율
    if not result["purpose"]:
        purpose_matches = list(_DART_PURPOSE_RE.finditer(content))
        if purpose_matches:
            result["purpose"] = purpose_matches[0].group(1).strip()
    if not result["period_from"]:
        period_matches = list(_DART_PERIOD_RE.finditer(content))
        if period_matches:
            result["period_from"] = period_matches[0].group(1)
            result["period_to"] = period_matches[0].group(2)

    return result


def _quarter_label(d) -> str:
    """date → 'Q2 2026' 형식."""
    if not d:
        return ""
    q = (d.month - 1) // 3 + 1
    return f"Q{q} {d.year}"


def _recency_label(issued_d, today_d) -> tuple[str, bool]:
    """발급추정일 → ('Q2 2026 · 50일 전', is_new). is_new = 7일 이내."""
    if not issued_d:
        return ("", False)
    delta_days = (today_d - issued_d).days
    qlabel = _quarter_label(issued_d)
    if delta_days < 0:  # 미래 (vld 가 이상한 경우)
        return (qlabel, False)
    if delta_days <= 7:
        return (f"🆕 NEW · {delta_days}일 전" if delta_days > 0 else "🆕 NEW · 오늘", True)
    if delta_days <= 30:
        return (f"{qlabel} · {delta_days}일 전", False)
    if delta_days <= 365:
        months = delta_days // 30
        return (f"{qlabel} · {months}개월 전", False)
    years = delta_days // 365
    return (f"{qlabel} · {years}년+ 전", False)


def _format_won(amount: int) -> str:
    """원화 정수 → '1,170억' / '1.17조' 표시. 0 이면 '미확인'."""
    if amount <= 0:
        return "미확인"
    if amount >= 1_000_000_000_000:  # 1조 이상
        trillions = amount / 1_000_000_000_000
        return f"{trillions:.2f}조" if trillions < 10 else f"{trillions:.1f}조"
    if amount >= 100_000_000:  # 1억 이상
        return f"{amount // 100_000_000:,}억"
    return f"{amount:,}원"


def _cats_for(it: dict) -> list[str]:
    """Article dict → 카테고리 멀티태그."""
    return categorize_tag(it.get("title", "") or "", it.get("content", "") or "")


def _date_range_md(items_lists: list[list[dict]]) -> tuple[str, str] | None:
    """여러 데이터셋의 published_at min/max → ("MM/DD", "MM/DD"). 없으면 None."""
    dates: list[str] = []
    for items in items_lists:
        for it in items:
            d = (it.get("published_at") or "")[:10]
            if d and len(d) == 10:
                dates.append(d)
    if not dates:
        return None
    return min(dates), max(dates)


def _mmdd(date_str: str) -> str:
    """YYYY-MM-DD → MM/DD."""
    if not date_str or len(date_str) < 10:
        return date_str or ""
    return f"{date_str[5:7]}/{date_str[8:10]}"


def _empty_block(period_label: str, what: str) -> str:
    """빈 섹션 placeholder."""
    return (
        f'<div class="empty-state">'
        f'📭 <strong>{html.escape(period_label)}</strong> 기간 동안 수집된 {html.escape(what)} 없음'
        f'</div>'
    )


def _render_chips(cats: list[str]) -> str:
    """카테고리 리스트 → HTML 칩 묶음."""
    chips = []
    for c in cats:
        color = CATEGORY_COLORS.get(c, "#5a6473")
        cls = "cchip gray" if c == "기타" else "cchip"
        style = "" if c == "기타" else f' style="background:{color};"'
        chips.append(f'<span class="{cls}"{style}>{html.escape(c)}</span>')
    return "".join(chips)


CSS = r"""
/* ===== 테마 변수 (3개) ===== */
:root, body.theme-dark {
  --bg:#0f1419; --bg-rgba:15,20,25;
  --panel:#1a1f29; --panel2:#222834; --border:#2a3140;
  --text:#e4e7ec; --text-soft:#cfd8e3; --muted:#8a93a3; --muted-soft:#9aa7b8;
  --link:#7cc4ff;
  --gold:#ffc857; --green:#5ec77a; --yellow:#e4b54a; --red:#e07b6f;
  --silver:#b8c1cf;
  --accent-good:#7ddfa3; --accent-warn:#ffb87a; --accent-bad:#ff7c7c;
  --accent-bad-soft:#ff9c9c; --accent-disabled:#a09080;
  --rev-bg:#1f5f3f; --rev-fg:#7ddfa3;       /* 매출 강조 chip */
  --correct-bg:#403030; --correct-fg:#ffb87a; /* 정정 chip */
  --info-bg:#2c3a5f; --info-fg:#8ab4f8;       /* 정보 chip (GMP건수 등) */
  --cdmo-bg:#3a2c5f; --cdmo-fg:#c084fc;       /* CDMO/바이오 분류 chip */
  --group-new-bg:#1a2030; --group-new-fg:#7ddfa3;
  --group-corr-bg:#2a201a; --group-corr-fg:#ffb87a;
  --row-hover:#1f2638; --shadow:0 4px 20px rgba(0,0,0,0.5);
}
body.theme-light {
  --bg:#ffffff; --bg-rgba:255,255,255;
  --panel:#f3f5f9; --panel2:#e6eaf1; --border:#cfd6e0;
  --text:#1a1f29; --text-soft:#2c3340; --muted:#586374; --muted-soft:#6b7585;
  --link:#0a66c2;
  --gold:#b07c00; --green:#1e8e44; --yellow:#a6791a; --red:#b3422f;
  --silver:#6b7585;
  --accent-good:#1e8e44; --accent-warn:#9c5d10; --accent-bad:#b3422f;
  --accent-bad-soft:#c95a45; --accent-disabled:#7a6b50;
  --rev-bg:#d9ecdf; --rev-fg:#0f5d2c;
  --correct-bg:#fae3c4; --correct-fg:#7a4810;
  --info-bg:#dce9f7; --info-fg:#0a4a8f;
  --cdmo-bg:#ece1f9; --cdmo-fg:#5a2e8e;
  --group-new-bg:#e5f4ea; --group-new-fg:#0f5d2c;
  --group-corr-bg:#fbf0dd; --group-corr-fg:#7a4810;
  --row-hover:#edf1f7; --shadow:0 4px 16px rgba(50,60,80,0.15);
}
body.theme-sepia {
  /* 블루라이트 차단 — 따뜻한 베이지/누런 톤 */
  --bg:#f5ecd3; --bg-rgba:245,236,211;
  --panel:#ebdcb2; --panel2:#dfc996; --border:#c8b27a;
  --text:#3d2f15; --text-soft:#5a4720; --muted:#7d6a3e; --muted-soft:#8a7a48;
  --link:#5a2f0a;
  --gold:#7a4f08; --green:#2f5418; --yellow:#6e4e10; --red:#7e2f15;
  --silver:#6f5d36;
  --accent-good:#2f5418; --accent-warn:#6e4e10; --accent-bad:#7e2f15;
  --accent-bad-soft:#9a4a2f; --accent-disabled:#8a7a48;
  --rev-bg:#cdbb84; --rev-fg:#2c4612;
  --correct-bg:#d4b074; --correct-fg:#5e370a;
  --info-bg:#cab78a; --info-fg:#2a3d6e;
  --cdmo-bg:#d0b8a4; --cdmo-fg:#4a1f6e;
  --group-new-bg:#ddc88c; --group-new-fg:#2c4612;
  --group-corr-bg:#d8b87a; --group-corr-fg:#5e370a;
  --row-hover:#dfc996; --shadow:0 4px 16px rgba(80,60,30,0.2);
}

/* ===== 폰트 사이즈 (3개, zoom 방식) ===== */
body.font-normal { zoom: 1; }
body.font-large { zoom: 1.13; }
body.font-xlarge { zoom: 1.28; }

* { box-sizing: border-box; }
html, body { margin:0; padding:0; background: var(--bg); color: var(--text);
  font-family: -apple-system, "Segoe UI", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
  font-size: 14px; line-height: 1.55;
  transition: background 0.2s, color 0.2s; }
a { color: var(--link); text-decoration: none; }
a:hover { text-decoration: underline; }
.container { max-width: 1200px; margin: 0 auto; padding: 24px; }

h1 { font-size: 24px; margin: 0 0 4px; }
h2 { font-size: 18px; margin: 32px 0 12px; padding-bottom: 8px; border-bottom: 1px solid var(--border); }
h3 { font-size: 15px; margin: 0 0 6px; }
.subtitle { color: var(--muted); margin-bottom: 24px; }

/* ===== 통합 sticky zone — nav + 카테고리 + 검색이 함께 따라다님 ===== */
.sticky-zone { position: sticky; top: 0; z-index: 100;
  background: rgba(var(--bg-rgba),.96); backdrop-filter: blur(8px);
  padding: 12px 0 8px; margin-bottom: 12px;
  border-bottom: 1px solid var(--border); }
/* 목차/nav 앵커 클릭 시 섹션 제목이 sticky 메뉴에 안 가리게 —
   scroll-padding-top 단일 메커니즘 (JS 가 실제 sticky 높이로 덮어씀, 폴백 170px).
   scroll-margin-top 과 병용하면 두 값이 합산돼 여백이 2배가 되므로 쓰지 않음. */
html { scroll-padding-top: 170px; }
.sticky-zone .nav { padding: 0 0 8px; }
.sticky-zone .catbar { margin: 8px 0 8px; padding: 8px 12px; }
.sticky-zone .filter { margin: 0; }

.nav a { display: inline-block; padding: 6px 12px; margin-right: 4px; margin-bottom: 4px;
  border-radius: 16px; background: var(--panel); color: var(--text); font-size: 13px; }
.nav a:hover { background: var(--panel2); text-decoration: none; }

/* Stat cards */
.stats { display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; margin-bottom: 20px; }
.stat { background: var(--panel); border-radius: 8px; padding: 14px 16px; border-left: 4px solid var(--border);
  display: block; text-decoration: none; color: inherit; cursor: pointer;
  transition: transform 0.12s ease, box-shadow 0.12s ease, background 0.12s ease; }
.stat:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.25); background: var(--panel-hover, var(--panel)); }
.stat .num { font-size: 28px; font-weight: 700; color: var(--text); }
.stat .label { font-size: 12px; color: var(--muted); margin-top: 2px; }
.stat.gold { border-left-color: var(--gold); }
.stat.silver { border-left-color: var(--silver); }
.stat.green { border-left-color: var(--green); }
.stat.yellow { border-left-color: var(--yellow); }
.stat.red { border-left-color: var(--red); }

/* Filter box */
.filter { margin: 16px 0; }
.filter-input-wrap { position: relative; }
.filter input { width: 100%; padding: 10px 38px 10px 14px;  /* 우측 패딩 = clear 버튼 자리 */
  background: var(--panel); color: var(--text);
  border: 1px solid var(--border); border-radius: 8px; font-size: 14px; }
.filter input:focus { outline: none; border-color: var(--link); }
.clear-search { position: absolute; right: 8px; top: 50%; transform: translateY(-50%);
  background: transparent; border: 0; cursor: pointer; padding: 3px 9px;
  font-size: 13px; line-height: 1; color: var(--muted); border-radius: 50%;
  display: none;  /* 입력 있을 때만 JS 가 표시 */
  transition: color .15s, background .15s; }
.clear-search:hover { color: var(--text); background: var(--panel2); }
.filter-input-wrap.has-text .clear-search { display: inline-block; }

/* Category filter bar */
.catbar { display: flex; flex-wrap: wrap; gap: 6px; margin: 12px 0 16px; padding: 10px 12px;
  background: var(--panel); border-radius: 8px; }
.catbar-label { color: var(--muted); font-size: 12px; padding: 6px 4px 6px 0; }
.catbtn { cursor: pointer; padding: 5px 12px; border-radius: 14px; background: var(--panel2);
  color: var(--text); font-size: 12px; border: 1px solid var(--border); user-select: none;
  display: inline-flex; align-items: center; gap: 4px; }
.catbtn:hover { background: #2d3645; }
.catbtn.active { background: var(--link); color: var(--bg); border-color: var(--link); font-weight: 600; }
.catbtn .cnt { opacity: 0.7; font-size: 11px; }
.catbtn.active .cnt { opacity: 1; }

/* Category chip on cards/rows */
.cchip { display: inline-block; padding: 1px 7px; border-radius: 9px; font-size: 11px;
  margin-right: 4px; color: #0f1419; font-weight: 600; }
.cchip.gray { background: var(--panel2); color: var(--text); font-weight: 400;
  border: 1px solid var(--border); }
/* 라이트/세피아 모드 — 컬러 칩 가독성 보강 */
body.theme-light .cchip:not(.gray),
body.theme-sepia .cchip:not(.gray) {
  text-shadow: 0 1px 0 rgba(255,255,255,0.4);
  border: 1px solid rgba(0,0,0,0.15);
}

/* 라이트/세피아 — 'badge' 도 가독성 확보 (.card .meta .badge 와 별개로 사용처 다양) */
body.theme-light .badge,
body.theme-sepia .badge {
  border: 1px solid var(--border);
}

/* Cards */
.card { background: var(--panel); border-radius: 8px; padding: 14px 16px; margin-bottom: 10px;
  border-left: 3px solid var(--border); }
.card.gold { border-left-color: var(--gold); }
.card.green { border-left-color: var(--green); }
.card.yellow { border-left-color: var(--yellow); }
.card .meta { font-size: 12px; color: var(--muted); margin: 4px 0; }
.card .meta .badge { display: inline-block; padding: 1px 8px; background: var(--panel2);
  border-radius: 10px; margin-right: 6px; }
.card .body { font-size: 13px; color: #c9cfdb; margin: 6px 0 4px; }
.card .url { font-size: 12px; word-break: break-all; }

/* Table */
table { width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 12px; }
th, td { padding: 8px 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
th { background: var(--panel); color: var(--muted); font-weight: 600; font-size: 12px;
  text-transform: uppercase; letter-spacing: 0.04em; }
tbody tr:hover { background: var(--panel); }
tbody tr[data-filterable]:not([data-section-id="mfds"]):hover { background: var(--row-hover); box-shadow: inset 3px 0 0 var(--accent-good); }
td.num-col { width: 40px; color: var(--muted); }

/* LOW list */
.low-row { padding: 4px 8px; font-size: 13px; color: var(--muted); border-bottom: 1px solid var(--border); }
.low-row .src { display: inline-block; padding: 1px 6px; background: var(--panel); border-radius: 4px;
  margin-right: 6px; font-size: 11px; }

/* Empty state */
.empty-state { background: var(--panel); border-radius: 8px; padding: 18px 20px;
  color: var(--muted); font-size: 14px; text-align: center; border: 1px dashed var(--border);
  margin-bottom: 12px; }
.empty-state strong { color: var(--text); }

/* 0-count 카테고리 칩 — 클릭은 되지만 약간 흐리게 */
.catbtn.zero { opacity: 0.45; }
.catbtn.zero:hover { opacity: 0.75; }
.catbtn.zero.active { opacity: 1; }

.footer { color: var(--muted); font-size: 12px; margin-top: 32px; padding-top: 16px;
  border-top: 1px solid var(--border); }

/* ===== 맨 위로 가는 버튼 (오른쪽 하단 플로팅) ===== */
.top-btn { position: fixed; right: 20px; bottom: 20px; z-index: 200;
  width: 44px; height: 44px; border-radius: 50%; cursor: pointer;
  background: var(--link); color: var(--bg); border: none;
  font-size: 20px; font-weight: bold; line-height: 1;
  box-shadow: var(--shadow); transition: opacity 0.2s, transform 0.2s;
  opacity: 0; pointer-events: none;
  display: flex; align-items: center; justify-content: center; }
.top-btn.visible { opacity: 0.85; pointer-events: auto; }
.top-btn:hover { opacity: 1; transform: translateY(-2px); }

/* ===== 플로팅 컨트롤 패널 (왼쪽 하단) — 테마/폰트 ===== */
.float-controls { position: fixed; left: 20px; bottom: 20px; z-index: 200;
  background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
  padding: 10px 12px; box-shadow: var(--shadow); font-size: 12px; }
.float-controls .group { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
.float-controls .group:last-child { margin-bottom: 0; }
.float-controls .lbl { color: var(--muted); font-size: 11px; margin-right: 4px;
  min-width: 36px; }
.float-controls button { cursor: pointer; padding: 5px 10px; border-radius: 6px;
  background: var(--panel2); color: var(--text); border: 1px solid var(--border);
  font-size: 11px; line-height: 1; transition: background 0.15s; }
.float-controls button:hover { background: var(--row-hover); }
.float-controls button.active { background: var(--link); color: var(--bg);
  border-color: var(--link); font-weight: 600; }

/* ===== 플로팅 목차 (오른쪽) ===== */
.float-toc { position: fixed; right: 20px; top: 80px; z-index: 90;
  background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
  padding: 10px 8px; box-shadow: var(--shadow); font-size: 12px;
  max-width: 200px; max-height: calc(100vh - 100px); overflow-y: auto; }
.float-toc .toc-title { color: var(--muted); font-size: 11px; padding: 2px 8px 6px;
  border-bottom: 1px solid var(--border); margin-bottom: 6px;
  text-transform: uppercase; letter-spacing: 0.05em; }
.float-toc a { display: block; padding: 5px 8px; margin-bottom: 1px;
  border-radius: 5px; color: var(--text); font-size: 12px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  border-left: 2px solid transparent; }
.float-toc a:hover { background: var(--panel2); text-decoration: none; }
.float-toc a.active { background: var(--row-hover); border-left-color: var(--link);
  color: var(--link); font-weight: 600; }
.float-toc a .cnt { color: var(--muted); font-size: 10.5px; margin-left: 4px; }
.float-toc a.active .cnt { color: var(--link); }

/* 참조 구분 라벨 — DART 2차 이하 (영업 직접 대상 아님) */
.float-toc .toc-sep { font-size: 10px; color: var(--muted); text-transform: uppercase;
  letter-spacing: 0.08em; margin: 6px 4px 4px; padding-top: 6px;
  border-top: 1px dashed var(--border); opacity: 0.75; }
.sticky-zone .nav .nav-sep { display: inline-block; padding: 6px 10px; margin: 0 4px 4px 8px;
  font-size: 10.5px; color: var(--muted); text-transform: uppercase;
  letter-spacing: 0.08em; opacity: 0.75; border-left: 1px dashed var(--border); }

/* ===== 섹션별 액센트 컬러 — TOC dot + 즐겨찾기 태그 공통 사용 ===== */
.sec-color-dart1    { --sec-accent: var(--gold); }          /* DART 1차 = 금 (시설투자) */
.sec-color-rss-high { --sec-accent: var(--green); }         /* 뉴스 HIGH = 녹 */
.sec-color-mfds     { --sec-accent: var(--cdmo-fg); }       /* MFDS = 보라 (제약/바이오) */
.sec-color-dart2    { --sec-accent: var(--muted-soft); }    /* DART 2차 = 흐림 */
.sec-color-rss-mid  { --sec-accent: var(--yellow); }        /* 뉴스 MID = 노 */
.sec-color-rss-low  { --sec-accent: var(--red); }           /* 뉴스 LOW = 빨 */
.sec-color-g2b      { --sec-accent: var(--silver); }        /* 나라장터 = 은 */

/* ===== float-toc 3-tier 시각 계층 ===== */
/* Tier 1 — 중요정보 (가장 강조: gold 배경 + bold) */
.float-toc a.toc-pin { font-weight: 700; color: var(--gold);
  background: rgba(255,200,87,0.10); border-left-color: var(--gold); padding-left: 10px; }
.float-toc a.toc-pin::before { content: '★ '; color: var(--gold); }
.float-toc a.toc-pin:hover { background: rgba(255,200,87,0.18); }

/* Tier 2 — 직접 영업 (1-4): 강조 텍스트 + 섹션 컬러 dot */
.float-toc a.toc-primary { color: var(--text); font-weight: 500; }
.float-toc a.toc-primary::before { content: ''; display:inline-block; width: 7px; height: 7px;
  background: var(--sec-accent); border-radius: 50%; margin-right: 7px;
  vertical-align: middle; box-shadow: 0 0 0 1px rgba(0,0,0,0.15); }

/* Tier 3 — 참조 (5-8): 회색·작게·살짝 흐림 */
.float-toc a.toc-ref { color: var(--muted); font-size: 11.5px; opacity: 0.78; }
.float-toc a.toc-ref:hover { opacity: 1; color: var(--text-soft); }
.float-toc a.toc-ref::before { content: ''; display:inline-block; width: 5px; height: 5px;
  background: var(--sec-accent); border-radius: 50%; margin-right: 6px;
  vertical-align: middle; opacity: 0.7; }

/* ===== sticky-zone nav 3-tier (인라인 칩 스타일) ===== */
.sticky-zone .nav a.toc-pin { font-weight: 700; color: var(--gold);
  background: rgba(255,200,87,0.14); }
.sticky-zone .nav a.toc-pin:hover { background: rgba(255,200,87,0.22); }
.sticky-zone .nav a.toc-primary { font-weight: 500; }
.sticky-zone .nav a.toc-primary::before { content: ''; display:inline-block; width: 6px; height: 6px;
  background: var(--sec-accent); border-radius: 50%; margin-right: 5px; vertical-align: middle; }
.sticky-zone .nav a.toc-ref { opacity: 0.65; color: var(--muted); font-size: 11.5px;
  background: transparent; }
.sticky-zone .nav a.toc-ref:hover { opacity: 1; color: var(--text-soft); background: var(--panel2); }
.sticky-zone .nav a.toc-ref::before { content: ''; display:inline-block; width: 5px; height: 5px;
  background: var(--sec-accent); border-radius: 50%; margin-right: 4px; vertical-align: middle;
  opacity: 0.6; }

/* ===== MFDS 전용 기간 필터 버튼 (시도 통계 박스 아래) ===== */
.mfds-period-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
  margin: 8px 0 12px; padding: 8px 12px; background: var(--panel);
  border: 1px solid var(--border); border-radius: 8px; }
.mfds-period-label { font-size: 12px; color: var(--muted); margin-right: 6px; }
.mfds-period-btn { cursor: pointer; padding: 5px 12px; border-radius: 14px;
  background: var(--panel2); color: var(--text); font-size: 12px;
  border: 1px solid var(--border); user-select: none; line-height: 1; }
.mfds-period-btn:hover { background: #2d3645; }
.mfds-period-btn.active { background: var(--cdmo-fg); color: var(--bg);
  border-color: var(--cdmo-fg); font-weight: 600; }
body.theme-light .mfds-period-btn:hover, body.theme-sepia .mfds-period-btn:hover {
  background: var(--row-hover);
}

/* ===== 즐겨찾기 패널 fav-tag — 섹션 컬러 적용 ===== */
.favorites-pinned .fav-item .fav-tag {
  /* 기본 muted 폴백, sec-color-X 클래스가 --sec-accent 를 주입하면 그 컬러 사용 */
  color: var(--sec-accent, var(--muted));
  border: 1px solid var(--sec-accent, var(--border));
  background: transparent;
  opacity: 0.95;
}

@media (max-width: 1400px) {
  /* 좁은 화면에선 목차 자동 축소 */
  .float-toc { right: 8px; max-width: 160px; font-size: 11px; }
}
@media (max-width: 1100px) {
  /* 더 좁으면 목차 숨김 (스티키 nav 가 있으니 손실 적음) */
  .float-toc { display: none; }
}

/* ===== 즐겨찾기 (★) — 행 좌측 별 + 맨 위 고정 패널 ===== */
.fav-star { background: transparent; border: 0; cursor: pointer; padding: 0 4px;
  font-size: 14px; line-height: 1; color: var(--muted); user-select: none;
  vertical-align: middle; opacity: 0.45; transition: opacity .15s, color .15s; }
.fav-star:hover { opacity: 1; }
.fav-star.on { color: var(--gold); opacity: 1; }
td.num-col .fav-star { margin-right: 2px; }

/* ===== 상단 대시보드 (KPI 카드 + TOP10 스코어링) ===== */
.dashboard { margin: 0 0 22px; }
.kpi-row { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
.kpi-card { flex: 1; min-width: 150px; background: var(--panel); border: 1px solid var(--gold);
            border-radius: 10px; padding: 15px 18px; }
.kpi-num { font-size: 30px; font-weight: 800; line-height: 1.1; }
.kpi-label { font-size: 13px; font-weight: 600; color: var(--text); margin-top: 5px; }
.kpi-sub { font-size: 11px; color: var(--muted); margin-top: 2px; }
.top10-wrap { background: var(--panel); border: 1px solid var(--gold); border-radius: 10px; padding: 14px 18px; }
.top10-title { font-size: 16px; margin: 0 0 10px; border: 0; padding: 0; }
.top10-table { width: 100%; border-collapse: collapse; }
.top10-table th { text-align: left; font-size: 11.5px; color: var(--muted); padding: 6px 8px; border-bottom: 2px solid var(--border); }
.top10-table td { padding: 9px 8px; border-bottom: 1px solid var(--border); font-size: 13px; vertical-align: top; }
.src-badge { font-size: 11px; padding: 2px 8px; border-radius: 5px; background: var(--row-hover); color: var(--text); white-space: nowrap; }
.favorites-pinned { background: var(--panel); border: 1px solid var(--gold);
  border-radius: 10px; padding: 14px 18px; margin: 16px 0 22px;
  box-shadow: 0 2px 14px rgba(255,200,87,0.10); }
.favorites-pinned h2 { margin: 0 0 8px; border: 0; padding: 0; font-size: 16px;
  color: var(--gold); }
.favorites-pinned .fav-hint { color: var(--muted); font-size: 11.5px; margin-bottom: 6px; }
.favorites-pinned .fav-empty { color: var(--muted); font-size: 13px; padding: 6px 0; }
.favorites-pinned .fav-item { display: flex; align-items: center; gap: 8px;
  padding: 6px 4px; border-bottom: 1px dashed var(--border); font-size: 13px; }
.favorites-pinned .fav-item:last-child { border-bottom: 0; }
.favorites-pinned .fav-item .fav-tag { display:inline-block; padding: 1px 7px;
  background: var(--panel2); border-radius: 4px; font-size: 11px; color: var(--muted);
  white-space: nowrap; min-width: 64px; text-align: center; }
.favorites-pinned .fav-item .fav-title { flex: 1; color: var(--text);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.favorites-pinned .fav-item .fav-title a { color: var(--text); }
.favorites-pinned .fav-item .fav-title a:hover { color: var(--link); }
.favorites-pinned .fav-item .fav-jump { font-size: 11px; color: var(--link);
  text-decoration: none; padding: 2px 8px; border-radius: 4px;
  background: var(--panel2); white-space: nowrap; }
.favorites-pinned .fav-item .fav-jump:hover { background: var(--row-hover); text-decoration: none; }
.favorites-pinned .fav-item .fav-remove { background: transparent; border: 0;
  cursor: pointer; color: var(--muted); padding: 2px 6px; font-size: 12px; }
.favorites-pinned .fav-item .fav-remove:hover { color: var(--accent-bad); }

/* ===== 섹션 접기/펴기 — h2[data-collapsible] ===== */
h2.collapsible { cursor: pointer; user-select: none; }
h2.collapsible::after { content: ' ▾'; font-size: 13px; color: var(--muted);
  margin-left: 6px; display: inline-block; }
h2.collapsible.collapsed::after { content: ' ▸'; }
h2.collapsible:hover { color: var(--link); }
.sec-body.hidden { display: none; }
"""

JS = r"""
// 상태: 활성화된 카테고리 (null = 전체) + 키워드 쿼리 + MFDS 기간 (0=전체, >0=N일 이내)
let activeCategory = null;
let activeQuery = '';
let activeMfdsPeriod = 0;

function applyFilter() {
  const term = activeQuery.toLowerCase().trim();
  const cat = activeCategory;
  document.querySelectorAll('[data-filterable]').forEach(el => {
    const hay = (el.getAttribute('data-search') || '').toLowerCase();
    const cats = (el.getAttribute('data-categories') || '').split(',').map(s => s.trim()).filter(Boolean);
    const okTerm = !term || hay.includes(term);
    const okCat = !cat || cats.includes(cat);
    // MFDS 전용 발급추정일 기간 필터 — 0 이면 전체, 양수면 N일 이내. -1(미상) 은 전체일 때만 표시
    let okMfds = true;
    if (activeMfdsPeriod > 0 && el.getAttribute('data-section-id') === 'mfds') {
      const d = parseInt(el.getAttribute('data-mfds-min-days') || '-1', 10);
      okMfds = (d >= 0 && d <= activeMfdsPeriod);
    }
    el.style.display = (okTerm && okCat && okMfds) ? '' : 'none';
  });
  // 그룹 헤더 — 해당 그룹에 visible row 가 0 이면 헤더도 숨김
  document.querySelectorAll('tr.group-header[data-group]').forEach(gh => {
    const group = gh.getAttribute('data-group');
    const rows = document.querySelectorAll(`tr[data-filterable][data-group="${group}"]`);
    const anyVisible = Array.from(rows).some(r => r.style.display !== 'none');
    gh.style.display = anyVisible ? '' : 'none';
  });
  // 섹션별 가시 카운트 + 동적 빈 상태 표시
  document.querySelectorAll('h2[data-section]').forEach(h => {
    const sec = h.getAttribute('data-section');
    const items = document.querySelectorAll(`[data-section-id="${sec}"][data-filterable]`);
    let visible = 0;
    items.forEach(it => { if (it.style.display !== 'none') visible++; });
    const tag = h.querySelector('.visible-count');
    if (tag) tag.textContent = (visible !== items.length) ? ` (${visible}/${items.length} 표시)` : '';
    // 필터 적용 후 0건 → 동적 placeholder. 원래 0건은 서버사이드 placeholder 그대로.
    const dynEmpty = document.getElementById(`empty-${sec}`);
    if (dynEmpty) {
      // 원본 데이터 자체가 0건이면 그대로 둔다 (서버사이드 placeholder 우선)
      const wasOriginallyEmpty = dynEmpty.getAttribute('data-original') === '1';
      if (wasOriginallyEmpty) {
        dynEmpty.style.display = '';
      } else {
        dynEmpty.style.display = (visible === 0 && items.length > 0) ? '' : 'none';
      }
    }
  });
}

function attachFilter() {
  const q = document.getElementById('q');
  const clearBtn = document.getElementById('q-clear');
  const wrap = q ? q.closest('.filter-input-wrap') : null;
  function syncClear(){ if (wrap) wrap.classList.toggle('has-text', q.value.length > 0); }
  if (q) {
    q.addEventListener('input', () => {
      activeQuery = q.value;
      syncClear();
      applyFilter();
    });
    // Esc 로도 지우기 (포커스가 input 안일 때)
    q.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && q.value) {
        q.value = ''; activeQuery = ''; syncClear(); applyFilter();
      }
    });
    syncClear();
  }
  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      if (!q) return;
      q.value = ''; activeQuery = ''; syncClear(); applyFilter(); q.focus();
    });
  }
  document.querySelectorAll('.catbtn').forEach(btn => {
    btn.addEventListener('click', () => {
      const cat = btn.getAttribute('data-cat');
      // 같은 버튼 다시 클릭 = 해제
      if (activeCategory === cat) {
        activeCategory = null;
      } else {
        activeCategory = cat || null;
      }
      document.querySelectorAll('.catbtn').forEach(b => b.classList.remove('active'));
      if (activeCategory !== null) btn.classList.add('active');
      else document.querySelector('.catbtn[data-cat=""]').classList.add('active');
      applyFilter();
    });
  });
}
// 행 전체 클릭 → data-url 새창 열기 (MFDS 제외).
// 내부 <a>(MFDS 링크 셀), 카테고리 칩(필터), 텍스트 드래그는 침해 안 함.
function attachRowClick() {
  document.querySelectorAll('table tr[data-filterable]').forEach(tr => {
    const sec = tr.getAttribute('data-section-id');
    if (sec === 'mfds') return;  // MFDS는 행 클릭 비활성 (링크 3개라 모호)
    const url = tr.getAttribute('data-url');
    if (!url) return;
    tr.style.cursor = 'pointer';
    tr.addEventListener('click', (e) => {
      if (e.target.closest('a')) return;       // 링크 자체 클릭은 기본 동작
      if (e.target.closest('.cchip')) return;  // 카테고리 칩 클릭은 필터 동작
      if (window.getSelection && window.getSelection().toString()) return;  // 텍스트 드래그 중이면 무시
      window.open(url, '_blank', 'noopener');
    });
  });
}

// ===== 테마 토글 (dark / light / sepia) =====
const THEMES = ['theme-dark', 'theme-light', 'theme-sepia'];
function setTheme(name) {
  document.body.classList.remove(...THEMES);
  document.body.classList.add(name);
  try { localStorage.setItem('sujoo_theme', name); } catch (e) {}
  document.querySelectorAll('button[data-theme]').forEach(b => {
    b.classList.toggle('active', b.getAttribute('data-theme') === name);
  });
}

// ===== 폰트 사이즈 토글 (normal / large / xlarge) =====
const FONTS = ['font-normal', 'font-large', 'font-xlarge'];
function setFont(name) {
  document.body.classList.remove(...FONTS);
  document.body.classList.add(name);
  try { localStorage.setItem('sujoo_font', name); } catch (e) {}
  document.querySelectorAll('button[data-font]').forEach(b => {
    b.classList.toggle('active', b.getAttribute('data-font') === name);
  });
}

// ===== 플로팅 목차 — 현재 보이는 섹션 하이라이트 =====
function attachToc() {
  const sections = document.querySelectorAll('h2[data-section]');
  if (!sections.length) return;
  // 현재 가장 위쪽 화면에 보이는 섹션을 active 로
  function update() {
    let activeSec = null;
    const thresholdY = 200;  // sticky-zone 아래 첫 화면 영역
    sections.forEach(h => {
      const rect = h.getBoundingClientRect();
      if (rect.top <= thresholdY) activeSec = h.getAttribute('data-section');
    });
    document.querySelectorAll('.float-toc a').forEach(a => {
      const sec = a.getAttribute('data-toc');
      a.classList.toggle('active', sec === activeSec);
    });
  }
  window.addEventListener('scroll', update, { passive: true });
  update();
}

// ===== 즐겨찾기 (★) — 행 왼쪽 별 + 맨 위 고정 패널 =====
const FAV_STORAGE_KEY = 'sujoo_favorites_v1';
const SEC_LABEL = {
  'g2b':'나라장터','dart1':'DART 1차','dart2':'DART 2차',
  'rss-high':'뉴스 HIGH','rss-mid':'뉴스 MID','rss-low':'뉴스 LOW','mfds':'식약처 GMP'
};
function loadFavs(){
  try { return new Set(JSON.parse(localStorage.getItem(FAV_STORAGE_KEY) || '[]')); }
  catch(e){ return new Set(); }
}
function saveFavs(s){
  try { localStorage.setItem(FAV_STORAGE_KEY, JSON.stringify([...s])); } catch(e){}
}
function cssEsc(s){
  // 우리 fid 는 md5 hex / url — 일반적으로 특수문자 없음. CSS.escape 가 있으면 사용.
  s = String(s == null ? '' : s);
  if (window.CSS && CSS.escape) return CSS.escape(s);
  return s.replace(/"/g, '\\"');
}
function attachFavorites(){
  const favs = loadFavs();
  document.querySelectorAll('tr[data-fav-id]').forEach(tr => {
    const numTd = tr.querySelector('td.num-col');
    if (!numTd || numTd.querySelector('.fav-star')) return;
    const fid = tr.getAttribute('data-fav-id');
    const btn = document.createElement('button');
    btn.className = 'fav-star' + (favs.has(fid) ? ' on' : '');
    btn.type = 'button';
    btn.title = '중요정보로 고정/해제';
    btn.textContent = favs.has(fid) ? '★' : '☆';
    btn.addEventListener('click', (e) => { e.stopPropagation(); toggleFav(fid); });
    numTd.insertBefore(btn, numTd.firstChild);
  });
  renderFavorites();
}
function toggleFav(fid){
  const favs = loadFavs();
  if (favs.has(fid)) favs.delete(fid); else favs.add(fid);
  saveFavs(favs);
  document.querySelectorAll('tr[data-fav-id="'+cssEsc(fid)+'"]').forEach(tr => {
    const btn = tr.querySelector('.fav-star');
    if (btn){
      const on = favs.has(fid);
      btn.classList.toggle('on', on);
      btn.textContent = on ? '★' : '☆';
    }
  });
  renderFavorites();
}
function _escHtml(s){
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function renderFavorites(){
  const list = document.getElementById('fav-list');
  if (!list) return;
  const favs = loadFavs();
  const items = [];
  document.querySelectorAll('tr[data-fav-id]').forEach(tr => {
    const fid = tr.getAttribute('data-fav-id');
    if (!favs.has(fid)) return;
    items.push({
      fid,
      title: tr.getAttribute('data-fav-title') || '(제목 없음)',
      url: tr.getAttribute('data-url') || '',
      sec: tr.getAttribute('data-section-id') || ''
    });
  });
  const cntToc = document.getElementById('fav-count-toc');
  if (cntToc) cntToc.textContent = String(items.length);
  const cntNav = document.getElementById('fav-count-nav');
  if (cntNav) cntNav.textContent = String(items.length);
  if (items.length === 0){
    list.innerHTML = '<div class="fav-empty">아직 고정된 정보가 없습니다. 각 행 왼쪽의 ☆ 별을 누르면 여기로 모입니다.</div>';
    return;
  }
  list.innerHTML = items.map(it => {
    const safeTitle = _escHtml(it.title);
    const safeUrl = _escHtml(it.url);
    const safeFid = _escHtml(it.fid);
    const tag = SEC_LABEL[it.sec] || it.sec || '';
    const titleHtml = it.url
      ? '<a href="'+safeUrl+'" target="_blank" rel="noopener">'+safeTitle+'</a>'
      : safeTitle;
    const safeSec = _escHtml(it.sec);
    return '<div class="fav-item">'
      + '<span class="fav-tag sec-color-'+safeSec+'">'+_escHtml(tag)+'</span>'
      + '<span class="fav-title">'+titleHtml+'</span>'
      + '<a class="fav-jump" href="#" data-jump="'+safeFid+'">↓ 본문</a>'
      + '<button class="fav-remove" type="button" data-rm="'+safeFid+'" title="해제">✕</button>'
      + '</div>';
  }).join('');
  list.querySelectorAll('.fav-jump').forEach(a => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      const fid = a.getAttribute('data-jump');
      const target = document.querySelector('tr[data-fav-id="'+cssEsc(fid)+'"]');
      if (!target) return;
      const sec = target.getAttribute('data-section-id');
      const h2 = document.querySelector('h2[data-section="'+cssEsc(sec)+'"]');
      if (h2 && h2.classList.contains('collapsed')) toggleCollapse(h2);
      target.scrollIntoView({behavior:'smooth', block:'center'});
      target.style.transition = 'background .3s';
      const prev = target.style.background;
      target.style.background = 'rgba(255,200,87,0.22)';
      setTimeout(()=>{ target.style.background = prev; }, 1400);
    });
  });
  list.querySelectorAll('.fav-remove').forEach(b => {
    b.addEventListener('click', (e) => { e.preventDefault(); toggleFav(b.getAttribute('data-rm')); });
  });
}

// ===== 섹션 접기/펴기 (h2[data-collapsible]) =====
function attachCollapsibles(){
  document.querySelectorAll('h2[data-collapsible]').forEach(h => {
    // h2 다음 h2 직전까지 형제 요소들을 .sec-body 로 감싼다
    const body = document.createElement('div');
    body.className = 'sec-body';
    body.setAttribute('data-body-for', h.getAttribute('data-section') || '');
    let n = h.nextElementSibling;
    while (n && n.tagName !== 'H2') {
      const next = n.nextElementSibling;
      body.appendChild(n);
      n = next;
    }
    if (n) h.parentNode.insertBefore(body, n);
    else h.parentNode.appendChild(body);
    h.classList.add('collapsible');
    // 기본 접힘 — localStorage 에서 복원
    const sec = h.getAttribute('data-section');
    let collapsed = true;
    try {
      const v = localStorage.getItem('sujoo_collapse_'+sec);
      if (v !== null) collapsed = (v === '1');
    } catch(e){}
    setCollapsed(h, body, collapsed);
    h.addEventListener('click', () => toggleCollapse(h));
  });
}
function setCollapsed(h, body, collapsed){
  h.classList.toggle('collapsed', collapsed);
  body.classList.toggle('hidden', collapsed);
}
function toggleCollapse(h){
  const sec = h.getAttribute('data-section') || '';
  const body = document.querySelector('.sec-body[data-body-for="'+cssEsc(sec)+'"]');
  if (!body) return;
  const collapsed = !h.classList.contains('collapsed');
  setCollapsed(h, body, collapsed);
  try { localStorage.setItem('sujoo_collapse_'+sec, collapsed ? '1' : '0'); } catch(e){}
}
// MFDS 전용 기간 필터 버튼 — applyFilter 의 activeMfdsPeriod 갱신
function attachMfdsPeriod(){
  const btns = document.querySelectorAll('.mfds-period-btn');
  btns.forEach(b => {
    b.addEventListener('click', () => {
      btns.forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      activeMfdsPeriod = parseInt(b.getAttribute('data-mfds-period') || '0', 10);
      applyFilter();
    });
  });
}

// 목차/nav 앵커 클릭 시 대상 섹션이 접혀있으면 자동 펼치기
function attachAnchorAutoExpand(){
  document.querySelectorAll('a[href^="#"]').forEach(a => {
    a.addEventListener('click', () => {
      const id = (a.getAttribute('href') || '').slice(1);
      if (!id) return;
      const target = document.getElementById(id);
      if (!target || target.tagName !== 'H2') return;
      if (target.classList.contains('collapsed')) toggleCollapse(target);
    });
  });
}

document.addEventListener('DOMContentLoaded', () => {
  // 저장된 테마/폰트 복원 (기본: dark + normal)
  let savedTheme = 'theme-dark', savedFont = 'font-normal';
  try {
    savedTheme = localStorage.getItem('sujoo_theme') || 'theme-dark';
    savedFont = localStorage.getItem('sujoo_font') || 'font-normal';
  } catch (e) {}
  setTheme(THEMES.includes(savedTheme) ? savedTheme : 'theme-dark');
  setFont(FONTS.includes(savedFont) ? savedFont : 'font-normal');

  // 플로팅 컨트롤 핸들러
  document.querySelectorAll('button[data-theme]').forEach(b => {
    b.addEventListener('click', () => setTheme(b.getAttribute('data-theme')));
  });
  document.querySelectorAll('button[data-font]').forEach(b => {
    b.addEventListener('click', () => setFont(b.getAttribute('data-font')));
  });

  attachFilter();
  attachRowClick();
  attachFavorites();          // 별 버튼 주입 + 맨 위 패널 렌더
  attachCollapsibles();       // 4개 섹션(g2b/dart2/rss-mid/rss-low) 기본 접힘
  attachAnchorAutoExpand();   // 목차/nav 클릭 시 접힌 섹션 자동 펼침
  attachMfdsPeriod();         // MFDS 전용 기간 필터 버튼 (7/30/90/180/365일)
  attachToc();
  // 기본 "전체" 버튼 활성화
  const allBtn = document.querySelector('.catbtn[data-cat=""]');
  if (allBtn) allBtn.classList.add('active');
});
"""


def _group_mfds_by_company(items: list[dict]) -> list[tuple[str, list[dict]]]:
    """MFDS items 를 bssh(업체명) 기준으로 그룹화. 정규화 키로 묶음.

    같은 회사 GMP 여러 건 → 카드 1장. 그룹 내부는 유효기간 늦은 순 유지.
    그룹 순서는 첫 등장 순서(=호출 측이 매출 desc 로 정렬해뒀음) 보존.
    """
    groups: dict[str, list[dict]] = {}
    order: list[str] = []  # 첫 등장 순서 유지
    for it in items:
        key = _normalize_corp_name(it.get("bssh") or "")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(it)
    return [(k, groups[k]) for k in order]


def render_mfds_company_row(company_items: list[dict], idx: int, today_d=None) -> str:
    """MFDS GMP — 회사 1행, GMP N건은 한 셀 안에 컴팩트 stacked.

    DART 2차 표와 일관된 룩. 한 회사가 여러 GMP 가져도 행 1개 → 스크롤 압축.
    today_d 가 주어지면 회사의 가장 최근 GMP 발급추정일까지의 일수를 data-mfds-min-days
    속성에 부여 → 화면 기간 필터 (7/30/90/180/365일) 가 행 단위로 동적 toggle.
    """
    from urllib.parse import quote
    first = company_items[0]
    bssh_raw = (first.get("bssh") or "업체미상").strip()
    # 회사 그룹의 최소 일수 — 가장 최근 GMP 의 발급추정일과 today_d 의 차이.
    # 미상(발급추정일 없음)이면 -1. 기간 필터 JS 가 -1 은 '전체' 옵션에서만 표시.
    mfds_min_days = -1
    if today_d is not None:
        for _ci in company_items:
            _s = (_ci.get("issued_est") or "").strip()
            if len(_s) >= 10:
                try:
                    _d = datetime.strptime(_s[:10], "%Y-%m-%d").date()
                    _delta = (today_d - _d).days
                    if _delta >= 0 and (mfds_min_days == -1 or _delta < mfds_min_days):
                        mfds_min_days = _delta
                except ValueError:
                    pass
    bssh = _esc(bssh_raw)
    bizrno = _esc(first.get("bizrno") or "")
    # 카테고리는 그룹 내 모든 item 합집합 (한 회사가 여러 카테고리 가질 수 있음).
    cats_set: list[str] = []
    for _it in company_items:
        for c in _cats_for(_it) or []:
            if c not in cats_set:
                cats_set.append(c)
    cats = cats_set or ["제약/바이오"]
    # MFDS GMP 데이터 본질이 제약/바이오 — 의미 있는 카테고리와 같이 잡힌 "기타" 는 노이즈로 제거.
    if len(cats) > 1 and "기타" in cats:
        cats = [c for c in cats if c != "기타"]
    # 그래도 "기타" 단독이면 제약/바이오로 강제
    if cats == ["기타"]:
        cats = ["제약/바이오"]
    # 매출 뱃지 — DART DB 매칭
    matched = _match_pharma_company(bssh_raw)
    if matched:
        _, info = matched
        rev = info.get("revenue", 0)
        if rev > 0:
            rev_label = f"{rev // 100_000_000:,}억"
            rev_badge = f'<span class="badge" style="background:var(--rev-bg);color:var(--rev-fg);font-weight:600;">매출: {rev_label}</span>'
        else:
            rev_badge = '<span class="badge" style="background:var(--cdmo-bg);color:var(--cdmo-fg);font-weight:600;">분류: CDMO/바이오</span>'
    else:
        rev_badge = ""
    n = len(company_items)
    count_badge = (f'<span class="badge" style="background:var(--info-bg);color:var(--info-fg);font-weight:600;">GMP: {n}건</span>'
                   if n > 1 else "")
    # 그룹 내 GMP 중 7일 이내 신규가 하나라도 있으면 회사 셀에 NEW 뱃지
    today_d = datetime.now(KST).date()
    group_has_new = False
    for _it in company_items:
        s = (_it.get("issued_est") or "").strip()
        if len(s) >= 10:
            try:
                d = datetime.strptime(s[:10], "%Y-%m-%d").date()
                if 0 <= (today_d - d).days <= 7:
                    group_has_new = True
                    break
            except ValueError:
                pass
    new_badge = ('<span class="badge" style="background:var(--accent-good);color:var(--bg);font-weight:700;">🆕 NEW (7일 이내 신규)</span>'
                 if group_has_new else "")
    # 회사 단위 링크 — 한 번만 (rowspan 첫 행에)
    naver_url = f"https://search.naver.com/search.naver?query={quote(bssh_raw + ' GMP 공장 증설')}"
    google_url = f'https://www.google.com/search?q={quote(bssh_raw + " " + chr(34) + "GMP 적합판정" + chr(34))}'
    # 정렬 파라미터 — 의약품안전나라 JS 코드 분석으로 발견.
    # 첫 로드에선 server-side 가 무시하고 가나다순으로 뜨지만, hidden input 에 박혀있어서
    # 사용자가 "허가일" 컬럼 한 번 클릭하면 의도한 정렬로 즉시 전환됨.
    # (식약처 시스템이 GET URL 로 deep-link 정렬을 막아놓음 — 우리가 줄 수 있는 최선)
    mfds_nedrug_url = (f"https://nedrug.mfds.go.kr/searchDrug?searchYn=true&itemName="
                       f"&entpName={quote(bssh_raw)}"
                       f"&sort=ITEM_PERMIT_DATE&sortOrder=false")
    # 검색용 필드 (회사 그룹 전체)
    all_addrs = " ".join((it.get("addr") or "") for it in company_items)
    all_forms = " ".join((it.get("form") or "") for it in company_items)
    search = f"{bssh} {bizrno} {all_addrs} {all_forms} {' '.join(cats)}"
    cats_attr = _esc(','.join(cats))
    search_attr = _esc(search)

    # 회사 셀 (rowspan=N 으로 첫 행에만)
    company_cell = (
        f'<td style="vertical-align:top;min-width:170px;border-right:1px solid #2a3140;" rowspan="{n}">'
        f'<div style="font-weight:600;font-size:13px;margin-bottom:4px;">{bssh}</div>'
        f'<div style="display:flex;gap:4px;flex-wrap:wrap;">'
        f'{new_badge}{_render_chips(cats)}{rev_badge}{count_badge}'
        f'</div>'
        f'{f"<div style=\"font-size:11px;color:var(--muted);margin-top:4px;\">사업자번호: {bizrno}</div>" if bizrno else ""}'
        f'</td>'
    )
    # 링크 셀 (rowspan=N 으로 첫 행에만)
    links_cell = (
        f'<td style="vertical-align:top;white-space:nowrap;font-size:11.5px;border-left:1px solid #2a3140;" rowspan="{n}">'
        f'<a href="{_esc(mfds_nedrug_url)}" target="_blank" rel="noopener" style="display:block;padding:1px 0;color:var(--accent-good);font-weight:600;">🏛️ 식약처 의약품안전나라</a>'
        f'<a href="{_esc(naver_url)}" target="_blank" rel="noopener" style="display:block;padding:1px 0;">🔍 네이버 검색</a>'
        f'<a href="{_esc(google_url)}" target="_blank" rel="noopener" style="display:block;padding:1px 0;">🌐 구글 검색</a>'
        f'</td>'
    )
    # 번호 셀 (rowspan=N)
    idx_cell = f'<td class="num-col" style="vertical-align:top;" rowspan="{n}">{idx}</td>'
    # 발주가능성 점수 셀 (회사 단위, rowspan=N) — 매출(발주 여력)+최근 GMP+제약/바이오 적합도 기반
    _sc = _mfds_score(company_items)
    _sc_color = _GRADE_COLORS.get(_sc["grade"], "#888")
    score_cell = (
        f'<td class="score-col" data-score="{_sc["score"]}" rowspan="{n}" '
        f'style="text-align:center;white-space:nowrap;vertical-align:top;">'
        f'<span style="font-weight:800;color:{_sc_color};font-size:15px;">{_sc["score"]}</span>'
        f'<div style="font-size:10px;font-weight:700;color:{_sc_color};letter-spacing:.5px;">{_sc["grade"]}</div></td>'
    )

    # GMP 별 행 — 첫 행에는 회사/번호/링크 셀 포함, 나머지 행은 GMP 셀만
    rows: list[str] = []
    for row_i, it in enumerate(company_items):
        addr_raw = (it.get("addr") or "").strip()
        kind = _esc(it.get("kind") or "")
        form_raw = (it.get("form") or "").strip()
        form_short = _esc(form_raw[:60] + "…") if len(form_raw) > 60 else _esc(form_raw)
        vld = _esc(it.get("vld") or "")
        issued_raw = (it.get("issued_est") or "").strip()
        issued_est = _esc(issued_raw)
        issued_d = None
        if len(issued_raw) >= 10:
            try:
                issued_d = datetime.strptime(issued_raw[:10], "%Y-%m-%d").date()
            except ValueError:
                pass
        recency, is_item_new = _recency_label(issued_d, today_d)
        # 발급추정일 셀 — NEW 강조
        if is_item_new:
            issued_cell = (
                f'<td style="vertical-align:top;white-space:nowrap;">'
                f'<b style="color:var(--accent-good);">{issued_est}</b>'
                f'<div style="font-size:11px;margin-top:2px;"><span style="background:var(--accent-good);color:var(--bg);padding:1px 6px;border-radius:3px;font-weight:700;">{recency}</span></div>'
                f'</td>'
            )
        else:
            issued_cell = (
                f'<td style="vertical-align:top;white-space:nowrap;">'
                f'<span style="color:var(--accent-good);">{issued_est}</span>'
                f'{f"<div style=\"font-size:11px;color:var(--muted);margin-top:2px;\">{recency}</div>" if recency else ""}'
                f'</td>'
            )
        # 공장 소재지 셀 (주소 + 지도 링크)
        kakao_url = f"https://map.kakao.com/?q={quote(addr_raw)}" if addr_raw else ""
        if addr_raw:
            addr_cell = (
                f'<td style="vertical-align:top;font-size:12px;">'
                f'{_esc(addr_raw)}'
                f'{f"<div style=\"margin-top:2px;\"><a href=\"{_esc(kakao_url)}\" target=\"_blank\" rel=\"noopener\" style=\"color:var(--link);font-size:11px;font-weight:600;\">🗺️ 지도 보기</a></div>" if kakao_url else ""}'
                f'</td>'
            )
        else:
            addr_cell = '<td style="vertical-align:top;color:var(--muted);">—</td>'

        # 셀 4개: 구분 / 제형 / 발급추정일 / 유효기간 / 공장 소재지
        kind_cell = f'<td style="vertical-align:top;white-space:nowrap;font-size:12px;"><b>{kind}</b></td>'
        form_cell = f'<td style="vertical-align:top;font-size:12px;color:var(--text);">{form_short or "—"}</td>'
        vld_cell = f'<td style="vertical-align:top;white-space:nowrap;font-size:12px;color:var(--muted);">{vld}</td>'

        # 행 조립 — 첫 행은 rowspan 셀 포함, 나머지는 GMP 셀만
        if row_i == 0:
            # 즐겨찾기 — MFDS 는 회사 단위로 1개 별 (첫 행 rowspan 안). fav-id 는 회사 식별자.
            mfds_fid = 'mfds:' + (first.get('id') or bssh_raw)
            mfds_fav_title = bssh_raw + (f' ({n}건)' if n > 1 else '')
            row_html = (
                f'<tr data-filterable data-fav-id="{_esc(mfds_fid)}" data-fav-title="{_esc(mfds_fav_title)}" data-url="{_esc(first.get("url",""))}" data-section-id="mfds" data-mfds-min-days="{mfds_min_days}" data-search="{search_attr}" data-categories="{cats_attr}">'
                f'{idx_cell}{score_cell}{company_cell}'
                f'{kind_cell}{form_cell}{issued_cell}{vld_cell}{addr_cell}'
                f'{links_cell}'
                f'</tr>'
            )
        else:
            # 같은 그룹의 두 번째 이상 행 — 필터/검색 속성도 유지 (회사명 검색해도 모든 GMP 표시되게)
            row_html = (
                f'<tr data-filterable data-section-id="mfds" data-mfds-min-days="{mfds_min_days}" data-search="{search_attr}" data-categories="{cats_attr}">'
                f'{kind_cell}{form_cell}{issued_cell}{vld_cell}{addr_cell}'
                f'</tr>'
            )
        rows.append(row_html)
    return "".join(rows)


def render_mfds_stats(items: list[dict]) -> str:
    """MFDS 섹션 헤더 — 전체 GMP 분포 통계 박스 (시도/시군구/회사 Top)."""
    if not items:
        return ""
    sido = Counter()
    sgg = Counter()
    bssh = Counter()
    kgmp = Counter()
    for it in items:
        addr = (it.get("addr") or "").strip()
        parts = addr.split()
        sido[parts[0] if parts else "?"] += 1
        sgg[" ".join(parts[:2]) if len(parts) >= 2 else "?"] += 1
        bssh[(it.get("bssh") or "?").strip()] += 1
        kgmp[(it.get("kind") or "?").strip()] += 1

    def _table(title: str, ctr: Counter, n: int = 10) -> str:
        rows = "".join(
            f'<tr>'
            f'<td style="padding:4px 8px;border-bottom:1px solid var(--border);color:var(--text);">{html.escape(k)}</td>'
            f'<td style="padding:4px 8px;border-bottom:1px solid var(--border);text-align:right;color:var(--muted);font-variant-numeric:tabular-nums;">{v}</td>'
            f'</tr>'
            for k, v in ctr.most_common(n)
        )
        return f"""<div style="flex:1;min-width:220px;">
  <div style="font-weight:700;margin-bottom:6px;color:var(--accent-good);font-size:12.5px;letter-spacing:0.03em;">{html.escape(title)}</div>
  <table style="font-size:12px;width:100%;border-collapse:collapse;">{rows}</table>
</div>"""

    return f"""<div style="display:flex;gap:12px;flex-wrap:wrap;padding:14px;background:var(--panel);border:1px solid var(--border);border-radius:8px;margin-bottom:12px;color:var(--text);">
  {_table('시도 Top', sido, 8)}
  {_table('시군구 Top', sgg, 12)}
  {_table('업체 Top', bssh, 12)}
  {_table('완제/원료', kgmp, 5)}
</div>"""


def render_eais_card(it: dict, idx: int) -> str:
    """EAIS 건축인허가 — 본문 정형필드(주용도/건축구분/연면적/주소/건물명)를 열 분리.

    EAIS 세움터 deep-link 가 외부에서 404 떨어지는 게 검증돼서, 행 자체 클릭은 비활성하고
    각 row 끝에 3개 외부 링크 버튼(지도/세움터/구글검색)을 둔다.
    """
    from urllib.parse import quote
    title_raw = it.get("title", "") or ""
    published = (it.get("published_at", "") or "")[:10]
    content = it.get("content", "") or ""
    cats = _cats_for(it)

    fields = _extract_eais_fields(content)
    addr = fields["addr"]
    usage = fields["usage"]
    kind = fields["kind"]
    gross_area = fields["gross_area"]
    bldg_area = fields["bldg_area"]
    land_area = fields["land_area"]
    bldg_count = fields["bldg_count"]
    bldg_name = fields["bldg_name"]
    permit_date = fields["permit_date"] or published
    # 영업/시공 부가정보 (없으면 0/'')
    atch_bldg = fields["atch_bldg_count"]
    jiyuk = fields["jiyuk"]
    bc_rat = fields["bc_rat"]
    vl_rat = fields["vl_rat"]
    pkng = fields["pkng_count"]
    hhld = fields["hhld_count"]
    jimok = fields["jimok"]
    guyuk = fields["guyuk"]
    stcns_sched = fields["stcns_sched"]
    real_stcns = fields["real_stcns"]
    use_apr = fields["use_apr"]

    # 사업명 — title 안의 [지역] / «건물명» 제거하고 핵심만, fallback 으로 건물명
    # title 예: "[인천 연수구 송도동] 용도변경-제2종근린생활시설 «옥련동 308-19번지» — 인천광역시 연수구 옥련동 308-19번지"
    # 우선순위: 건물명(content 정형) → title 정리본
    region = ""
    m = re.match(r"\[([^\]]+)\]\s*(.+?)(?:\s*«|—|$)", title_raw)
    if m:
        region = m.group(1).strip()
        biz_name_raw = m.group(2).strip()
    else:
        biz_name_raw = title_raw
    biz_name = bldg_name or biz_name_raw or "—"

    # 연면적 강조 — 10,000㎡+ 는 강조
    area_label = _format_area(gross_area)
    if gross_area >= EAIS_LARGE_AREA_THRESHOLD:
        area_style = "font-weight:700;color:var(--accent-good);"
    elif gross_area > 0:
        area_style = "font-weight:600;"
    else:
        area_style = "color:var(--muted);"

    # 부속 면적 (건축면적/대지면적/주건축물수) — 작은 글씨 추가 정보
    extra_bits: list[str] = []
    if bldg_area > 0:
        extra_bits.append(f"건축 {_format_area(bldg_area)}")
    if land_area > 0:
        extra_bits.append(f"대지 {_format_area(land_area)}")
    if bldg_count > 1:
        extra_bits.append(f"{bldg_count}동")
    extra_html = (
        f'<div style="font-size:10.5px;color:var(--muted);font-weight:400;margin-top:1px;">'
        f'{" · ".join(_esc(b) for b in extra_bits)}</div>'
        if extra_bits else ""
    )

    # 주소 셀 — 상세 주소만. title 의 [카테고리 추정금액] 은 카테고리 칩 + 별도 추정공사비
    # 컬럼으로 이미 표시되니 region 부분(카테고리+금액)을 주소 셀에 굳이 또 넣지 않는다.
    # 지도 버튼은 같은 셀 inline (영업팀이 주소 보고 바로 클릭).
    if addr:
        addr_html = f'<span>{_esc(_preview(addr, 70))}</span>'
    else:
        addr_html = '<span style="color:var(--muted);">—</span>'

    usage_html = _esc(usage) if usage else '<span style="color:var(--muted);">—</span>'
    kind_html = _esc(kind) if kind else '<span style="color:var(--muted);">—</span>'

    # 외부 링크 버튼 — 지도(네이버) / 세움터
    # 검색어 우선순위: 상세주소 > region > 건물명 > biz_name_raw
    query_seed = addr or region or bldg_name or biz_name_raw or title_raw
    query_q = quote(query_seed) if query_seed else ""
    naver_url = f"https://map.naver.com/p/search/{query_q}" if query_q else ""
    # 세움터는 deep-link 가 막혀있음 (검색 페이지 직진 URL 도 외부 진입 시
    # 세션 검증 실패로 탭이 자동 닫힘). 메인 페이지로 보내고 사용자가 메뉴 클릭.
    eais_url = "https://www.eais.go.kr/"

    btn_css = ("display:inline-block;padding:3px 7px;margin-right:3px;font-size:11px;"
               "border:1px solid var(--border);border-radius:4px;text-decoration:none;"
               "color:var(--link);white-space:nowrap;background:var(--panel);")
    # 지도는 주소 옆에 inline 배치 (영업팀이 주소 보고 바로 누르도록)
    map_btn = (
        f'<a class="eais-btn" href="{_esc(naver_url)}" target="_blank" rel="noopener" '
        f'title="네이버 지도 주소 검색" style="{btn_css}margin-left:6px;">🗺️ 지도</a>'
    ) if naver_url else ""
    # 세움터 버튼만 끝 컬럼에. (구글은 영업 가치 적어 제거)
    other_btns = (
        f'<a class="eais-btn" href="{_esc(eais_url)}" target="_blank" rel="noopener" '
        f'title="세움터 메인 (주소 복사해서 검색)" style="{btn_css}">🏗️ 세움터</a>'
    )

    # data-url 비워서 행 클릭 비활성 (attachRowClick 이 빈 url 은 skip)
    # 영업/시공 부가정보 sub-line (사업명 셀 아래에 작은 글씨로)
    bits: list[str] = []
    if jiyuk:
        bits.append(f"🏭 {jiyuk}")
    if guyuk:
        bits.append(f"📍 {guyuk}")   # 경제자유구역·지구단위계획구역 등
    if jimok and jimok not in ("대", "—"):
        bits.append(f"🗺️ 지목 {jimok}")  # 공장용지·창고용지 등 (대지/임야는 평범해서 생략)
    if pkng > 0:
        bits.append(f"🅿️ {pkng:,}대")
    if bc_rat > 0 and vl_rat > 0:
        bits.append(f"📐 건폐 {bc_rat:.1f}% / 용적 {vl_rat:.1f}%")
    elif bc_rat > 0:
        bits.append(f"📐 건폐 {bc_rat:.1f}%")
    elif vl_rat > 0:
        bits.append(f"📐 용적 {vl_rat:.1f}%")
    if atch_bldg > 0:
        bits.append(f"+부속{atch_bldg}동")
    if hhld > 0:
        bits.append(f"⚠️ 세대 {hhld}")  # 0이 아니면 주거용 가능성 (영업 우선순위 낮음)
    sub_info_html = (
        f'<div style="font-size:10.5px;color:var(--muted);font-weight:400;margin-top:2px;">'
        f'{" · ".join(_esc(b) for b in bits)}</div>'
        if bits else ""
    )

    # 인허가일 셀에 단계 표시 — 영업 골든타임 가늠:
    #   실착공일 있음 → 시공사 이미 결정 (영업 너무 늦음, 회색)
    #   착공예정일만 있음 → 인허가 단계 (영업 골든타임, 주황)
    #   사용승인일 있음 → 준공 완료 (영업 대상 아님, 회색)
    if use_apr:
        stage_html = f'<div style="font-size:10.5px;color:var(--muted);margin-top:1px;">🏁 사용승인 {use_apr}</div>'
    elif real_stcns:
        stage_html = f'<div style="font-size:10.5px;color:var(--muted);margin-top:1px;">⚙️ 착공 {real_stcns}<br/><span style="color:var(--accent-warn);">시공사 확정됨</span></div>'
    elif stcns_sched:
        stage_html = f'<div style="font-size:10.5px;color:var(--accent-good);margin-top:1px;font-weight:600;">⏳ 착공예정 {stcns_sched}<br/>영업 골든타임</div>'
    else:
        stage_html = ""

    search = f"{title_raw} {region} {addr} {usage} {kind} {bldg_name} {jiyuk} {guyuk} {jimok} {' '.join(cats)}"
    return f"""<tr data-filterable data-fav-id="{_esc(it.get('id') or it.get('url') or '')}" data-fav-title="{_esc(it.get('title',''))}" data-section-id="eais" data-url="" data-search="{_esc(search)}" data-categories="{_esc(','.join(cats))}">
  <td class="num-col">{idx}</td>
  <td>{_render_chips(cats)} <b>{_esc(_preview(biz_name, 80))}</b>{sub_info_html}</td>
  <td style="font-size:12px;">{usage_html}</td>
  <td style="white-space:nowrap;font-size:12px;">{kind_html}</td>
  <td style="text-align:right;white-space:nowrap;{area_style}">{area_label}{extra_html}</td>
  <td style="font-size:12px;">{addr_html}{map_btn}</td>
  <td style="white-space:nowrap;">{permit_date}{stage_html}</td>
  <td style="white-space:nowrap;text-align:right;">{other_btns}</td>
</tr>"""


def render_g2b_card(it: dict, idx: int) -> str:
    """G2B 입찰공고 — 한 행짜리 표 row."""
    title = _esc(it.get("title", ""))
    published = (it.get("published_at", "") or "")[:10]
    content = it.get("content", "") or ""
    url = _esc(it.get("url", ""))
    cats = _cats_for(it)

    fields = _extract_g2b_fields(content)
    notice = fields["notice"]
    demand = fields["demand"]
    price = fields["price"]
    open_date = fields["open_date"]
    open_time = fields["open_time"]

    # 발주처 셀 — 수요기관(실제 발주)을 강조, 공고기관(중간자, 조달청 등)은 다를 때만 작게
    if demand and notice and demand != notice:
        issuer_html = (
            f'<div style="font-weight:600;">{_esc(demand)}</div>'
            f'<div style="font-size:11px;color:var(--muted);">via {_esc(notice)}</div>'
        )
    elif demand:
        issuer_html = f'<div style="font-weight:600;">{_esc(demand)}</div>'
    elif notice:
        issuer_html = f'<div style="font-weight:600;">{_esc(notice)}</div>'
    else:
        issuer_html = '<span style="color:var(--muted);">—</span>'

    # 추정가격 — 500억+ 강조 / 0원(=미공개)은 muted 라벨로 명시
    if price > 0:
        price_label = _format_won(price)
    elif "0원" in content:
        price_label = '미공개<div style="font-size:10.5px;color:var(--muted);font-weight:400;">(0원)</div>'
    else:
        price_label = "—"
    if price >= G2B_PRICE_THRESHOLD:
        price_style = "font-weight:700;color:var(--accent-good);"
    elif price == 0:
        price_style = "color:var(--accent-disabled);font-weight:500;"
    else:
        price_style = "font-weight:600;"

    # 개찰일시 — 날짜/시간 두 줄
    if open_date and open_time:
        time_short = open_time[:5]  # HH:MM:SS → HH:MM
        open_html = (
            f'<div>{open_date}</div>'
            f'<div style="font-size:11px;color:var(--muted);">{time_short}</div>'
        )
    elif open_date:
        open_html = f'<div>{open_date}</div>'
    else:
        open_html = '<span style="color:var(--muted);">—</span>'

    search = f"{title} {notice} {demand} {price_label} {' '.join(cats)}"
    return f"""<tr data-filterable data-fav-id="{_esc(it.get('id') or it.get('url') or '')}" data-fav-title="{_esc(it.get('title',''))}" data-section-id="g2b" data-url="{url}" data-search="{_esc(search)}" data-categories="{_esc(','.join(cats))}">
  <td class="num-col">{idx}</td>
  <td>{_render_chips(cats)} <b>{title}</b></td>
  <td style="font-size:12px;">{issuer_html}</td>
  <td style="text-align:right;white-space:nowrap;{price_style}">{price_label}</td>
  <td style="white-space:nowrap;font-size:11.5px;">{open_html}</td>
  <td style="white-space:nowrap;">{published}</td>
</tr>"""


_GRADE_COLORS = {"S": "#e8453c", "A": "#f59e0b", "B": "#8a94a6", "C": "#b8bfca"}


def _dart_item_score(it: dict) -> dict:
    """DART 1차 항목 발주가능성 점수 (신규시설투자 + 유형자산취득 공용)."""
    if _is_dart_asset_acquisition(it):
        f = _extract_dart_asset_fields(it.get("content") or "")
        amount = f.get("amount", 0) or 0
        equity = None  # 자산취득은 '자산총액대비'라 시설투자 자본대비와 의미 달라 미반영
        site = bool(f.get("target") or f.get("target_name") or f.get("purpose"))
    else:
        f = _extract_dart_invest_fields(it.get("content") or "")
        amount = f.get("amount", 0) or 0
        raw = f.get("equity_ratio") or ""
        try:
            equity = float(raw) if raw else None
        except ValueError:
            equity = None
        site = bool(f.get("invest_target"))
    return score_opportunity(
        source="dart1", amount_won=amount, categories=_cats_for(it),
        is_new=not _is_dart_correction(it), equity_ratio=equity, has_site=site,
    )


def _score_cell(sc: dict) -> str:
    """발주가능성 점수/등급 테이블 셀 (전 섹션 공통)."""
    g = sc["grade"]
    color = _GRADE_COLORS.get(g, "#888")
    return (f'<td class="score-col" data-score="{sc["score"]}" '
            f'style="text-align:center;white-space:nowrap;">'
            f'<span style="font-weight:800;color:{color};font-size:15px;">{sc["score"]}</span>'
            f'<div style="font-size:10px;font-weight:700;color:{color};letter-spacing:.5px;">{g}</div></td>')


# 자이씨앤에이 비시공 영역 — 뉴스 HIGH 에서 강등 (조선/해양플랜트는 조선소 건조물이라 시공 대상 아님)
_OUT_OF_SCOPE_KEYWORDS = (
    "FLNG", "부유식 액화", "해양플랜트", "해양설비", "조선소", "LNG운반선",
    "시추선", "드릴십", "원유운반선", "해상풍력", "송전선로",
)

# 명확한 '시공' 신호 — 뉴스 HIGH 자격. 이게 없으면 단순 수주/투자/물량/펀드로 보고 강등.
_CONSTRUCTION_ACTIONS = {
    "착공", "기공", "신축", "증설", "신설", "준공", "완공", "착수", "확장", "확충",
}

# 명백한 비(非)시설 뉴스 — 제목에 이 단어가 있으면 발주가능성 점수 강제 강등(C).
# 금융·증시·실적 / 사건·사고·법 / 인사·행사·홍보 / 정부지원·국책 → 공장발주와 무관.
# (카테고리가 우연히 매칭돼 시설점수가 붙는 오탐 방지. 룰 기반이라 영업팀 피드백으로 가감.)
_NEWS_NOISE_TITLE = (
    # 금융/증시/주가/자본
    "비트코인", "코인", "가상자산", "암호화폐", "주가", "상한가", "하한가", "급등", "급락",
    "코스피", "코스닥", "증시", "시가총액", "공매도", "유상증자", "무상증자", "전환사채",
    "펀드", "출자", "지분", "M&A", "인수합병",
    # 실적/회계
    "실적", "영업이익", "순이익", "어닝", "적자전환", "흑자전환", "자본잠식", "배당",
    # 사건/사고/법/노사
    "압수수색", "압색", "참사", "화재", "폭발사고", "붕괴", "파업", "리콜", "소송", "고발",
    "구속", "기소", "과징금", "담합", "횡령", "배임", "안전점검", "제재",
    # 인사/행사/홍보/수상
    "선임", "취임", "사임", "별세", "수상", "간담회", "기자회견", "컨퍼런스", "세미나",
    "포럼", "전시회", "박람회", "컴퓨텍스",
    # 정책/예산 (발주가 아닌 정부 지원·국책)
    "국비", "국책", "보조금",
    # 거시경제/지표 (공장발주 무관)
    "성장률", "GDP", "물가", "환율", "금리", "수출입", "무역수지",
    # 홍보/기념 (창립·주년 등 PR성)
    "창립", "주년", "출범식",
    # 의견·정책 기사 (칼럼·사설·약가 등 — 발주 아님)
    "칼럼", "사설", "기고", "오피니언", "약가",
    # === 2026-06-05 증시 테마·수혜주 hype (주식 기사 — 공장발주 아님) ===
    "수혜주", "관련주", "테마주", "대장주", "급등주", "유망주",
    "수혜 기업", "수혜기업", "로봇주", "들썩",
    # 칼럼·기획 시리즈 (제목 머리 대괄호) — 분석·전망 기사
    "[포커스", "[차이나", "[심층", "[집중분석", "[기획", "[이슈",
    "[르포", "패권전쟁",
    # 정치·정책 연재 / 시황·분석 칼럼 (산업 언급해도 발주 아님)
    "[이재명", "[윤석열", "[AI 생태계", "생태계 전쟁", "밸류체인 한계",
    "밖에 없", "잭팟", "수혜 기대", "기대 만발", "사업 순항", "순항 중",
)

# 실적/매출 기사 패턴 — 단어 하나로는 못 잡는 '매출 N% 성장', 'N% 증가' 등 (제목 대상).
# 투자/발주가 아닌 회계 성과 기사 → 발주가능성 점수 강등.
_NEWS_NOISE_RE = re.compile(
    r"매출.{0,8}(성장|증가|감소|돌파|기록|급증|급감|역성장)"
    r"|\d+\s*%\s*(성장|증가|감소|급증|급감|돌파)"
)

# 매크로 아티팩트 가드 (2026-06-04): '2조 비즈니스 상한'은 제거했지만, 뉴스 본문 자유텍스트는
# 국가예산("에너지전환 39조")·해외펀딩·시장규모 같은 매크로 수치를 규모로 잘못 집어올 수 있다.
# 단일 공사 프로젝트는 현실적으로 10조를 안 넘으므로, 그 이상은 매크로로 보고 컷한다.
# (DART 시설투자 등 정형 금액엔 적용 안 함 — 거긴 10조+도 실제 메가프로젝트.)
_NEWS_MACRO_CEIL_WON = 10 * 1_000_000_000_000  # 10조

# 데이터센터는 '제목'에 명시될 때만 시설로 인정 — 본문에 납품처로만 언급된(ESS·장비 공급) 오분류 방지.
_DC_TITLE_RE = re.compile(r"데이터센터|IDC|하이퍼스케일|코로케이션|전산센터|클라우드데이터")


def _news_cats(it: dict) -> list[str]:
    """뉴스 전용 카테고리 — 데이터센터는 제목 매칭일 때만 인정 (DART엔 미적용)."""
    cats = _cats_for(it)
    if "데이터센터" in cats and not _DC_TITLE_RE.search(it.get("title", "") or ""):
        cats = [c for c in cats if c != "데이터센터"]
    return cats or ["기타"]


# ── 타 건설사 수주/낙찰 = 시공사 이미 확정 → 자이 영업 불가('뺏긴 건', 경쟁사 동향) ──
# 발주가능성(우리가 수주할 가능성) 관점에서 0 에 가까우므로 강등. DART 2차(공급계약)와 같은 논리.
_CONTRACTOR_NAMES = (
    "삼성물산", "현대건설", "현대엔지니어링", "GS건설", "지에스건설", "대우건설",
    "DL이앤씨", "디엘이앤씨", "DL건설", "포스코이앤씨", "롯데건설",
    "HDC현대산업개발", "현대산업개발", "SK에코플랜트", "호반건설", "금호건설",
    "태영건설", "두산건설", "계룡건설", "동부건설", "코오롱글로벌", "한신공영",
    "중흥토건", "서희건설", "쌍용건설", "삼성E&A", "삼성이앤에이", "한양건설",
    # 2026-06-05 추가 — DART2 공급계약 신고자로 등장한 타 건설사 (경쟁사 수주 동향)
    "KCC건설", "특수건설", "우원개발", "일성건설", "신세계건설", "동부엔지니어링",
)
_AWARDED_RE = re.compile(
    r"(신축|건설|건축|토목|플랜트|리모델링|증축|정비)\s*공사.{0,6}(수주|낙찰|도급|수의계약)"
    r"|공사\s*(수주|낙찰)"
    r"|시공권\s*확보|시공사\s*(선정|확정)|턴키\s*(수주|계약)|EPC\s*(수주|계약)"
)


def _news_competitor_win(it: dict) -> bool:
    """타 건설사가 이미 수주/낙찰 = 시공사 확정 → 우리 영업 기회 아님."""
    title = it.get("title", "") or ""
    if _AWARDED_RE.search(title):
        return True
    if any(c in title for c in _CONTRACTOR_NAMES) and re.search(r"수주|낙찰|시공|도급|착공|준공", title):
        return True
    return False


def _news_actions(it: dict) -> list[str]:
    """stage1 매칭 패턴에서 행동 키워드만 추출 (action:착공,증설 → ['착공','증설'])."""
    acts: list[str] = []
    for p in (it.get("stage1_matched_patterns") or []):
        if p.startswith("action:"):
            acts += p[len("action:"):].split(",")
    return acts


def _news_amount_won(text: str) -> int:
    """뉴스 본문 대표 금액(원) 추정 — '조'/'억' 표기 중 가장 큰 단일 값 (규모 점수용).

    (이전: '첫 조 + 최대 억' 합산 → '1조…최대 4조' 본문이 1.x조로 과소집계됐음. max 로 교정.)
    금액 상한(2조) 컷은 2026-06-04 영업팀 요청으로 제거 — 큰 프로젝트일수록 큰 기회.
    """
    vals: list[float] = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*조", text):
        vals.append(float(m.group(1)) * 1_000_000_000_000)
    for m in re.finditer(r"(\d[\d,]*)\s*억", text):
        try:
            vals.append(int(m.group(1).replace(",", "")) * 100_000_000)
        except ValueError:
            pass
    return int(max(vals)) if vals else 0


def _news_out_of_scope(it: dict) -> str:
    """뉴스 HIGH 강등 사유 (영업 범위 밖/시공신호 없음). 해당 없으면 빈 문자열."""
    text = f"{it.get('title','')} {it.get('content','') or ''}"
    title = it.get("title", "")
    # 제목이 명백한 비시설성(금융·실적·사건·인사·행사·국책 등)이면 본문에 '증설'이 섞여도 공사발주 아님
    if any(k in title for k in _NEWS_NOISE_TITLE) or _NEWS_NOISE_RE.search(title):
        return "비시설 뉴스(금융·실적·사건·인사·행사 등)"
    if _news_amount_won(text) >= _NEWS_MACRO_CEIL_WON:
        return "국가예산·매크로 규모(10조+) — 단일 공사 아님"
    if any(k in text for k in _OUT_OF_SCOPE_KEYWORDS):
        return "조선·해양·송전 등 비시공영역"
    if _news_competitor_win(it):
        return "타 건설사 수주/낙찰 — 시공사 확정(우리 영업 불가)"
    # 명확한 시공 신호(착공/신축/증설 등) 없는 단순 수주·투자·물량·펀드 → 공사수주 무관 가능성↑
    if not any(a in _CONSTRUCTION_ACTIONS for a in _news_actions(it)):
        return "시공 신호(착공/신축/증설) 없음 — 단순 수주/투자/물량"
    return ""


def _news_score(it: dict, source: str = "news_high") -> dict:
    """뉴스 항목 발주가능성 점수 (오탐 게이팅 포함).

    오탐 방지 2단:
      1) 제목이 명백한 비시설 뉴스(_NEWS_NOISE_TITLE: 비트코인·주가·실적·압수수색·
         전시회·국비 등) → 시설/규모 무효화 + C(<=25) 강등.
      2) 시설 적합도(카테고리)는 '실제 프로젝트' 정황이 있을 때만 인정 —
         시공행동(착공/증설 등)·금액·면적 중 하나라도 있어야 함. 카테고리만 우연히
         매칭된 분석·칼럼 기사는 시설점수 0 (base+규모미상 수준으로 떨어짐).
    """
    title = it.get("title", "") or ""
    text = f"{title} {it.get('content','') or ''}"
    patterns = it.get("stage1_matched_patterns") or []
    is_done = any(("준공" in p or "완공" in p)
                  for p in patterns if p.startswith("action:"))
    has_area = any(p.startswith("area:") for p in patterns)
    has_action = any(a in _CONSTRUCTION_ACTIONS for a in _news_actions(it))
    amount = _news_amount_won(text)
    # 하드 제외 — 영업 범위 밖이면 무조건 C 강등 (시설/규모 점수 무효화):
    #   ① 비시설 노이즈 제목(금융·실적·사건·인사·행사·국책·칼럼·약가)
    #   ② 조선·해양플랜트·송전 등 비시공영역
    #   ③ 10조+ = 국가예산·해외펀딩·시장규모 매크로 아티팩트 (단일 공사 아님)
    #   ④ 타 건설사 수주/낙찰 = 시공사 확정 → 우리 영업 불가('뺏긴 건')
    # (2조 비즈니스 상한은 2026-06-04 제거 — 큰 프로젝트일수록 큰 기회. 10조 가드만 유지)
    is_hard_oos = (any(k in title for k in _NEWS_NOISE_TITLE)
                   or _NEWS_NOISE_RE.search(title)
                   or amount >= _NEWS_MACRO_CEIL_WON
                   or any(k in text for k in _OUT_OF_SCOPE_KEYWORDS)
                   or _news_competitor_win(it))
    # 시설 적합도(카테고리=시설점수)는 '실제 시설 프로젝트' 정황이 있을 때만 인정한다 (2026-06-05).
    #   ① 강한 시설 명사(공장/플랜트/팹/데이터센터 등 STRONG_TARGETS)가 제목·본문에 있고
    #   ② 시공행동·금액·면적 중 하나라도 동반될 것.
    # 산업만 언급한 칼럼·시황·정책 기사("[이재명 정부] 관세협상", "반도체 수출 1조弗")는
    # 강한 시설 명사가 없어 facility 0 → base+규모 수준(대개 C)으로 떨어진다.
    # (시설 명사 없이 금액 토큰만으로 CR 시설점수 30이 붙어 칼럼이 A급에 오르던 오탐 차단.)
    has_strong_target = any(
        t in STRONG_TARGETS
        for p in patterns if p.startswith("target:")
        for t in p[len("target:"):].split(",")
    )
    corroborated = has_strong_target and (has_action or amount > 0 or has_area)
    cats = _news_cats(it) if (corroborated and not is_hard_oos) else []
    sc = score_opportunity(
        source=source, amount_won=(0 if is_hard_oos else amount), categories=cats,
        is_new=True, has_site=(has_area and not is_hard_oos), is_done=is_done,
    )
    if is_hard_oos:
        capped = min(sc["score"], 25)  # 영업 범위 밖은 무조건 C
        return {"score": capped, "grade": grade_for(capped), "breakdown": sc["breakdown"]}
    return sc


def _dart2_competitor_gc(it: dict) -> bool:
    """DART2 공급계약 신고자가 타 건설사 = 경쟁사가 이미 시공 수주 → 우리 영업 기회 아님.
    (장비·소재 공급사의 공급계약은 발주처가 시설을 짓는다는 동향 신호라 유지하지만,
     건설사 본인의 공급계약은 그 공사를 경쟁사가 가져간 것이므로 동향용 C로 강등.)"""
    corp = _dart_corp(it)
    return any(c in corp for c in _CONTRACTOR_NAMES)


def _dart2_score(it: dict) -> dict:
    """DART 2차 공급계약 발주가능성 점수 (이미 시공사 확정 — 협력사/경쟁사 동향용)."""
    sc = score_opportunity(
        source="dart2", amount_won=_extract_dart_contract_amount(it),
        categories=_cats_for(it), is_new=not _is_dart_correction(it), has_site=True,
    )
    if _dart2_competitor_gc(it):
        capped = min(sc["score"], 30)  # 경쟁 건설사 수주 — 시공 확정, C 동향용
        return {"score": capped, "grade": grade_for(capped), "breakdown": sc["breakdown"]}
    return sc


def _mfds_company_revenue(bssh: str) -> int:
    """식약처 회사명 → DART 매칭 매출(원). 매칭 실패(CDMO/바이오 등)면 0."""
    m = _match_pharma_company(bssh or "")
    return m[1].get("revenue", 0) if m else 0


def _mfds_score(company_items: list[dict]) -> dict:
    """식약처 GMP 회사 발주가능성 점수 (회사 그룹 단위).

    GMP 적합판정은 '투자 금액'이 없는 간접 신호(증설 여력)다. 따라서 규모(scale) 축에는
    회사 매출(=발주 여력 규모)을 대입한다 — 큰 제약사일수록 큰 공장 발주 가능성↑.
    가장 최근 GMP 발급추정일이 90일 이내면 '활성(신규)' 신호로 실현강도를 가산한다.
    부지 확보(has_site)는 기존 공장 주소일 뿐 신규 부지가 아니므로 False.
    """
    if not company_items:
        return score_opportunity(source="mfds", amount_won=0, categories=["제약/바이오"])
    first = company_items[0]
    revenue = _mfds_company_revenue((first.get("bssh") or "").strip())
    # 회사 그룹 카테고리 합집합 (없으면 제약/바이오). '기타' 노이즈 제거.
    cats: list[str] = []
    for it in company_items:
        for c in (_cats_for(it) or []):
            if c not in cats:
                cats.append(c)
    cats = [c for c in cats if c != "기타"] or ["제약/바이오"]
    # 가장 최근 발급추정일이 90일 이내면 활성 신호(=신규로 취급)
    today_d = datetime.now(KST).date()
    recent = False
    for it in company_items:
        s = (it.get("issued_est") or "").strip()
        if len(s) >= 10:
            try:
                d = datetime.strptime(s[:10], "%Y-%m-%d").date()
                if 0 <= (today_d - d).days <= 90:
                    recent = True
                    break
            except ValueError:
                pass
    return score_opportunity(
        source="mfds", amount_won=revenue, categories=cats,
        is_new=recent, equity_ratio=None, has_site=False,
    )


def render_dart_primary_card(it: dict, idx: int, group: str = "") -> str:
    """DART 1차 시설투자 결정 — 본문 정형필드(투자구분/금액/자본대비/목적/기간)를 열 분리."""
    title_raw = it.get("title", "")
    # title 앞에 [회사명] prefix 있으면 분리
    if title_raw.startswith("["):
        end = title_raw.find("]")
        corp = title_raw[1:end] if end > 0 else ""
        rpt = title_raw[end+1:].strip() if end > 0 else title_raw
    else:
        corp = ""
        rpt = title_raw
    published = (it.get("published_at", "") or "")[:10]
    content = it.get("content", "") or ""
    url = _esc(it.get("url", ""))
    cats = _cats_for(it)

    fields = _extract_dart_invest_fields(content)
    invest_type = fields["invest_type"]
    invest_target = fields["invest_target"]
    amount = fields["amount"]
    equity_ratio = fields["equity_ratio"]
    purpose = fields["purpose"]
    period_from = fields["period_from"]
    period_to = fields["period_to"]
    is_withdrawn = fields["is_withdrawn"]
    correction_reason = fields["correction_reason"]

    # 정정공시 + 철회 chip
    is_correction = "[기재정정]" in rpt or "[기재정정]" in title_raw
    correction_chip = (
        '<span class="cchip" style="background:var(--correct-bg);color:var(--correct-fg);">정정</span> '
        if is_correction else ""
    )
    withdraw_chip = (
        '<span class="cchip" style="background:var(--accent-bad);color:var(--bg);font-weight:600;">철회</span> '
        if is_withdrawn else ""
    )

    # 투자금액 — 500억+ 강조 (영업 단가 기준선) / 미확인은 muted
    amount_label = _format_won(amount) if amount > 0 else "—"
    if amount >= 50_000_000_000:
        amt_style = "font-weight:700;color:var(--accent-good);"
    elif amount == 0:
        amt_style = "color:var(--muted);"
    else:
        amt_style = "font-weight:600;"

    ratio_html = (
        f'<div style="font-size:11px;color:var(--muted);font-weight:400;">자본 {_esc(equity_ratio)}%</div>'
        if equity_ratio else ""
    )

    # 투자기간 — 시작/종료 두 줄로
    if period_from and period_to:
        period_html = (
            f'<div>{period_from}</div>'
            f'<div style="color:var(--muted);">~ {period_to}</div>'
        )
    else:
        period_html = '<span style="color:var(--muted);">—</span>'

    # 투자목적 셀 — 📍투자대상 + 본문 + (철회 시 정정사유 추가)
    purpose_blocks: list[str] = []
    if invest_target:
        purpose_blocks.append(
            f'<div style="color:var(--accent-good);font-weight:500;">'
            f'📍 {_esc(_preview(invest_target, 70))}</div>'
        )
    if purpose:
        purpose_blocks.append(f'<div>{_esc(_preview(purpose, 110))}</div>')
    # 철회 케이스 — 정정사유를 빨강으로 명시
    if is_withdrawn and correction_reason:
        purpose_blocks.append(
            f'<div style="color:var(--accent-bad);font-size:11.5px;">'
            f'⚠️ {_esc(_preview(correction_reason, 90))}</div>'
        )
    elif not purpose and correction_reason:
        # 정정공시 본 신고서 없음 — 정정사유 fallback
        purpose_blocks.append(
            f'<div style="color:var(--muted);font-size:11.5px;">'
            f'<b style="color:var(--accent-warn);">사유</b> {_esc(_preview(correction_reason, 90))}</div>'
        )
    purpose_html = "".join(purpose_blocks) or '<span style="color:var(--muted);">—</span>'

    # 구분 셀 = invest_type. 추출 실패 + 정정공시면 "정정사항만" 안내
    if invest_type:
        invest_type_html = _esc(invest_type)
    elif is_withdrawn:
        invest_type_html = '<span style="color:var(--accent-bad);font-size:11px;">투자 철회</span>'
    elif is_correction:
        invest_type_html = '<span style="color:var(--muted);font-size:11px;">정정사항만</span>'
    else:
        invest_type_html = '<span style="color:var(--muted);">—</span>'

    search = (f"{corp} {rpt} {invest_type} {invest_target} {purpose} "
              f"{correction_reason} {amount_label} {' '.join(cats)}")
    group_attr = f' data-group="{group}"' if group else ""
    score_cell = _score_cell(_dart_item_score(it))
    return f"""<tr data-filterable data-fav-id="{_esc(it.get('id') or it.get('url') or '')}" data-fav-title="{_esc(it.get('title',''))}" data-section-id="dart1"{group_attr} data-url="{url}" data-search="{_esc(search)}" data-categories="{_esc(','.join(cats))}">
  <td class="num-col">{idx}</td>
  {score_cell}
  <td style="white-space:nowrap;font-weight:600;">{_esc(corp)}</td>
  <td style="white-space:nowrap;">{correction_chip}{withdraw_chip}{invest_type_html}</td>
  <td style="text-align:right;white-space:nowrap;{amt_style}">{amount_label}{ratio_html}</td>
  <td style="font-size:12px;line-height:1.4;">{purpose_html}</td>
  <td style="white-space:nowrap;font-size:11.5px;">{period_html}</td>
  <td style="white-space:nowrap;">{published}</td>
</tr>"""


def render_dart_asset_card(it: dict, idx: int, group: str = "") -> str:
    """DART 1차 유형자산취득결정 — 본문 정형필드 분리.

    신규시설투자등(render_dart_primary_card) 과 동일한 7컬럼 구조를 공유하되,
    셀 내용 의미가 다름:
      구분=취득목적물 / 금액=취득금액(자산%) / 목적=취득목적(📍거래상대방+목적) /
      기간=취득예정일자(단일)
    """
    title_raw = it.get("title", "") or ""
    if title_raw.startswith("["):
        end = title_raw.find("]")
        corp = title_raw[1:end] if end > 0 else ""
        rpt = title_raw[end+1:].strip() if end > 0 else title_raw
    else:
        corp = ""
        rpt = title_raw
    published = (it.get("published_at", "") or "")[:10]
    content = it.get("content", "") or ""
    url = _esc(it.get("url", ""))
    cats = _cats_for(it)

    fields = _extract_dart_asset_fields(content)
    target = fields["target"]
    target_name = fields["target_name"]
    amount = fields["amount"]
    asset_ratio = fields["asset_ratio"]
    partner = fields["partner"]
    relation = fields["relation"]
    purpose = fields["purpose"]
    expected_date = fields["expected_date"]
    is_withdrawn = fields["is_withdrawn"]

    # 정정 chip + 철회 chip
    is_correction = "[기재정정]" in rpt or "[기재정정]" in title_raw
    correction_chip = (
        '<span class="cchip" style="background:var(--correct-bg);color:var(--correct-fg);">정정</span> '
        if is_correction else ""
    )
    withdraw_chip = (
        '<span class="cchip" style="background:var(--accent-bad);color:var(--bg);font-weight:600;">철회</span> '
        if is_withdrawn else ""
    )

    # 취득금액 — 500억+ 강조
    amount_label = _format_won(amount) if amount > 0 else "—"
    if amount >= 50_000_000_000:
        amt_style = "font-weight:700;color:var(--accent-good);"
    elif amount == 0:
        amt_style = "color:var(--muted);"
    else:
        amt_style = "font-weight:600;"
    ratio_html = (
        f'<div style="font-size:11px;color:var(--muted);font-weight:400;">자산 {_esc(asset_ratio)}%</div>'
        if asset_ratio else ""
    )

    # 취득예정일자 (단일 날짜) — 신규시설투자는 시작~종료 두 줄, 유형자산은 한 줄
    if expected_date:
        period_html = f'<div>{expected_date}</div><div style="color:var(--muted);font-size:10.5px;">취득 예정</div>'
    else:
        period_html = '<span style="color:var(--muted);">—</span>'

    # 취득목적 셀 — 📍거래상대방 + 📌물건명(상세위치) + 본 목적 텍스트
    purpose_inner = _esc(_preview(purpose, 110)) if purpose else ""
    blocks: list[str] = []
    if partner:
        partner_label = partner + (f" ({relation})" if relation else "")
        blocks.append(
            f'<div style="color:var(--accent-good);font-weight:500;">'
            f'📍 {_esc(_preview(partner_label, 60))}</div>'
        )
    if target_name:
        blocks.append(
            f'<div style="color:var(--accent-warn);font-size:11.5px;">'
            f'📌 {_esc(_preview(target_name, 70))}</div>'
        )
    if purpose_inner:
        blocks.append(f'<div>{purpose_inner}</div>')
    if is_withdrawn and not purpose_inner:
        blocks.append('<div style="color:var(--accent-bad);">⚠️ 양수 결정 철회됨</div>')
    purpose_html = "".join(blocks) if blocks else '<span style="color:var(--muted);">—</span>'

    # 구분 셀 = 취득목적물 (토지/건물/부동산 등). 본문 추출 실패 + 정정공시면 "정정사항만" 안내
    if target:
        target_html = _esc(_preview(target, 30))
    elif is_correction:
        target_html = '<span style="color:var(--muted);font-size:11px;">정정사항만</span>'
    elif is_withdrawn:
        target_html = '<span style="color:var(--accent-bad);font-size:11px;">양수 철회</span>'
    else:
        target_html = '<span style="color:var(--muted);">—</span>'

    search = (f"{corp} {rpt} {target} {target_name} {partner} {purpose} "
              f"{amount_label} {' '.join(cats)}")
    group_attr = f' data-group="{group}"' if group else ""
    score_cell = _score_cell(_dart_item_score(it))
    return f"""<tr data-filterable data-fav-id="{_esc(it.get('id') or it.get('url') or '')}" data-fav-title="{_esc(it.get('title',''))}" data-section-id="dart1"{group_attr} data-url="{url}" data-search="{_esc(search)}" data-categories="{_esc(','.join(cats))}">
  <td class="num-col">{idx}</td>
  {score_cell}
  <td style="white-space:nowrap;font-weight:600;">{_esc(corp)}</td>
  <td style="white-space:nowrap;">{correction_chip}{withdraw_chip}{target_html}</td>
  <td style="text-align:right;white-space:nowrap;{amt_style}">{amount_label}{ratio_html}</td>
  <td style="font-size:12px;line-height:1.4;">{purpose_html}</td>
  <td style="white-space:nowrap;font-size:11.5px;">{period_html}</td>
  <td style="white-space:nowrap;">{published}</td>
</tr>"""


def render_dart_secondary_row(it: dict, idx: int, group: str = "") -> str:
    """DART 2차 단일판매ㆍ공급계약체결 — 본문 정형필드(계약명/발주처/지역/금액/기간) 열 분리."""
    title = it.get("title", "") or ""
    if title.startswith("["):
        end = title.find("]")
        corp = title[1:end] if end > 0 else ""
        rpt = title[end+1:].strip() if end > 0 else title
    else:
        corp = ""
        rpt = title
    published = (it.get("published_at", "") or "")[:10]
    url = _esc(it.get("url", ""))
    cats = _cats_for(it)
    content = it.get("content", "") or ""

    amount = _extract_dart_contract_amount(it)
    amount_label = _format_won(amount)
    # 500억+ 강조 (DART 1차/G2B 와 동일 기준선)
    if amount >= DART_CONTRACT_THRESHOLD:
        amt_style = "font-weight:700;color:var(--accent-good);"
    elif amount == 0:
        amt_style = "color:var(--muted);"
    else:
        amt_style = "font-weight:600;"

    fields = _extract_dart_contract_fields(content)
    contract_kind = fields["contract_kind"]
    contract_name = fields["contract_name"]
    partner = fields["partner"]
    relation = fields["relation"]
    area = fields["area"]
    period_from = fields["period_from"]
    period_to = fields["period_to"]
    rev_pct = fields["rev_pct"]

    # 정정공시 chip (DART 1차와 동일 패턴)
    is_correction = "[기재정정]" in rpt or "[기재정정]" in title
    correction_chip = (
        '<span class="cchip" style="background:var(--correct-bg);color:var(--correct-fg);">정정</span> '
        if is_correction else ""
    )

    # 계약명 셀 — 구분 chip + 계약명 + (fallback: 공시명 rpt)
    kind_chip = (
        f'<span class="cchip gray" style="font-size:10.5px;">{_esc(contract_kind)}</span> '
        if contract_kind else ""
    )
    name_text = contract_name or rpt
    contract_html = (
        f'<div>{correction_chip}{kind_chip}{_esc(_preview(name_text, 80))}</div>'
    )

    # 발주처 셀 — 계약상대 + (계열관계 작게)
    if partner:
        relation_html = (
            f'<div style="font-size:11px;color:var(--accent-warn);font-weight:500;">{_esc(relation)}</div>'
            if relation and relation not in ("미해당", "해당") else ""
        )
        partner_html = (
            f'<div style="font-weight:600;">{_esc(_preview(partner, 50))}</div>'
            f'{relation_html}'
        )
    else:
        partner_html = '<span style="color:var(--muted);">—</span>'

    # 지역 셀
    area_html = _esc(_preview(area, 40)) if area else '<span style="color:var(--muted);">—</span>'

    # 매출 대비% — 금액 셀에 부속
    rev_html = (
        f'<div style="font-size:11px;color:var(--muted);font-weight:400;">매출 {_esc(rev_pct)}%</div>'
        if rev_pct else ""
    )

    # 계약기간 — 두 줄
    if period_from and period_to:
        period_html = (
            f'<div>{period_from}</div>'
            f'<div style="color:var(--muted);">~ {period_to}</div>'
        )
    else:
        period_html = '<span style="color:var(--muted);">—</span>'

    search = (f"{corp} {rpt} {contract_kind} {contract_name} {partner} "
              f"{area} {amount_label} {' '.join(cats)}")
    group_attr = f' data-group="{group}"' if group else ""
    score_cell = _score_cell(_dart2_score(it))
    return f"""<tr data-filterable data-fav-id="{_esc(it.get('id') or it.get('url') or '')}" data-fav-title="{_esc(it.get('title',''))}" data-section-id="dart2"{group_attr} data-url="{url}" data-search="{_esc(search)}" data-categories="{_esc(','.join(cats))}">
  <td class="num-col">{idx}</td>
  {score_cell}
  <td style="white-space:nowrap;font-weight:600;">{_esc(corp)}</td>
  <td style="font-size:12px;line-height:1.4;">{contract_html}</td>
  <td style="font-size:12px;">{partner_html}</td>
  <td style="font-size:12px;">{area_html}</td>
  <td style="text-align:right;white-space:nowrap;{amt_style}">{amount_label}{rev_html}</td>
  <td style="white-space:nowrap;font-size:11.5px;">{period_html}</td>
  <td style="white-space:nowrap;">{published}</td>
</tr>"""


def _render_match_cell(patterns, reason: str, limit: int = 4) -> str:
    """RSS 매칭 키워드 셀 — 한국어 키워드(행동/대상/금액) + 분류 사유."""
    kw_html = _format_match_patterns(patterns, limit=limit)
    parts = []
    if kw_html:
        parts.append(f'<div style="font-size:12px;line-height:1.5;">{kw_html}</div>')
    if reason:
        parts.append(
            f'<div style="margin-top:3px;font-size:11px;color:var(--muted);">'
            f'<b style="color:var(--muted-soft);">사유</b> {_esc(reason)}</div>'
        )
    return "".join(parts) or '<span style="color:var(--muted);">—</span>'


def render_rss_high_card(reason: str, it: dict, idx: int, source: str = "news_high") -> str:
    """RSS HIGH 뉴스 — 한 행짜리 표 row. source=분류에서 정한 base 출처(점수 일관성)."""
    title = _esc(it.get("title", ""))
    src = _esc(it.get("source", ""))
    published = (it.get("published_at", "") or "")[:10]
    content = it.get("content", "") or ""
    url = _esc(it.get("url", ""))
    patterns = (it.get("stage1_matched_patterns") or [])
    cats = _news_cats(it)
    match_cell = _render_match_cell(patterns, reason, limit=4)
    score_cell = _score_cell(_news_score(it, source))
    search = f"{title} {content} {src} {reason} {' '.join(patterns)} {' '.join(cats)}"
    return f"""<tr data-filterable data-fav-id="{_esc(it.get('id') or it.get('url') or '')}" data-fav-title="{_esc(it.get('title',''))}" data-section-id="rss-high" data-url="{url}" data-search="{_esc(search)}" data-categories="{_esc(','.join(cats))}">
  <td class="num-col">{idx}</td>
  {score_cell}
  <td>{src}</td>
  <td>{_render_chips(cats)} <b>{title}</b><div style="margin-top:2px;font-size:11.5px;color:var(--muted);">{_esc(_preview(content, 160))}</div></td>
  <td>{match_cell}</td>
  <td style="white-space:nowrap;">{published}</td>
</tr>"""


def render_rss_mid_row(reason: str, it: dict, idx: int, source: str = "news_mid") -> str:
    title = _esc(it.get("title", ""))
    src = _esc(it.get("source", ""))
    published = (it.get("published_at", "") or "")[:10]
    patterns = (it.get("stage1_matched_patterns") or [])
    url = _esc(it.get("url", ""))
    cats = _news_cats(it)
    match_cell = _render_match_cell(patterns, reason, limit=3)
    score_cell = _score_cell(_news_score(it, source))
    search = f"{title} {src} {reason} {' '.join(patterns)} {' '.join(cats)}"
    return f"""<tr data-filterable data-fav-id="{_esc(it.get('id') or it.get('url') or '')}" data-fav-title="{_esc(it.get('title',''))}" data-section-id="rss-mid" data-url="{url}" data-search="{_esc(search)}" data-categories="{_esc(','.join(cats))}">
  <td class="num-col">{idx}</td>
  {score_cell}
  <td>{src}</td>
  <td>{_render_chips(cats)} {title}</td>
  <td>{match_cell}</td>
  <td style="white-space:nowrap;">{published}</td>
</tr>"""


def render_rss_low_row(reason: str, it: dict, idx: int, source: str = "news_low") -> str:
    title = _esc(it.get("title", ""))
    src = _esc(it.get("source", ""))
    published = (it.get("published_at", "") or "")[:10]
    url = _esc(it.get("url", ""))
    patterns = (it.get("stage1_matched_patterns") or [])
    cats = _news_cats(it)
    match_cell = _render_match_cell(patterns, reason, limit=3)
    score_cell = _score_cell(_news_score(it, source))
    search = f"{title} {src} {reason} {' '.join(patterns)} {' '.join(cats)}"
    return f"""<tr data-filterable data-fav-id="{_esc(it.get('id') or it.get('url') or '')}" data-fav-title="{_esc(it.get('title',''))}" data-section-id="rss-low" data-url="{url}" data-search="{_esc(search)}" data-categories="{_esc(','.join(cats))}">
  <td class="num-col">{idx}</td>
  {score_cell}
  <td>{src}</td>
  <td>{_render_chips(cats)} {title}</td>
  <td>{match_cell}</td>
  <td style="white-space:nowrap;">{published}</td>
</tr>"""


def _dart_corp(it: dict) -> str:
    """DART title '[회사명] 보고서명' 에서 회사명 추출."""
    t = it.get("title", "") or ""
    if t.startswith("["):
        e = t.find("]")
        if e > 0:
            return t[1:e]
    return t


def _news_source_for(label: str) -> str:
    """classify_rss 섹션 라벨 → score_opportunity base 출처. (점수의 base 일관성용.)"""
    return {"HIGH": "news_high", "MID": "news_mid", "LOW": "news_low"}.get(label, "news_mid")


def _collect_scored(dart_invest: list, dart_asset: list,
                    rss_high: list, dart_secondary: list) -> list[dict]:
    """전 섹션 항목을 발주가능성 점수로 통합 — 상단 대시보드(KPI/TOP10)용.
    rss_high 는 (reason, it, source) 3-튜플 (등급 S/A 뉴스)."""
    out: list[dict] = []
    for it in dart_invest + dart_asset:
        sc = _dart_item_score(it)
        amt = (_dart_asset_amount(it) if _is_dart_asset_acquisition(it)
               else _dart_invest_amount(it))
        f = _extract_dart_invest_fields(it.get("content") or "")
        out.append({"score": sc["score"], "grade": sc["grade"], "src": "DART 1차",
                    "corp": _dart_corp(it), "proj": f.get("invest_target") or f.get("purpose") or "",
                    "cats": _cats_for(it), "amount": amt, "url": it.get("url", "")})
    for _reason, it, _src in rss_high:
        sc = _news_score(it, _src)
        text = f"{it.get('title','')} {it.get('content','') or ''}"
        out.append({"score": sc["score"], "grade": sc["grade"], "src": "뉴스 HIGH",
                    "corp": _preview(it.get("title", ""), 40), "proj": "",
                    "cats": _news_cats(it), "amount": _news_amount_won(text), "url": it.get("url", "")})
    for it in dart_secondary:
        sc = _dart2_score(it)
        out.append({"score": sc["score"], "grade": sc["grade"], "src": "DART 2차",
                    "corp": _dart_corp(it), "proj": "", "cats": _cats_for(it),
                    "amount": _extract_dart_contract_amount(it), "url": it.get("url", "")})
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def render_dashboard(scored: list[dict]) -> str:
    """상단 대시보드 — KPI 4카드 + TOP10 스코어링 테이블 (영업팀 화면예시 구현)."""
    if not scored:
        return ""
    s_cnt = sum(1 for x in scored if x["grade"] == "S")
    a_cnt = sum(1 for x in scored if x["grade"] == "A")
    invest = sum(x["amount"] for x in scored if x["grade"] in ("S", "A") and x["amount"])
    invest_label = _format_won(invest) if invest else "—"

    def _card(num, label, sub, color):
        return (f'<div class="kpi-card"><div class="kpi-num" style="color:{color};">{num}</div>'
                f'<div class="kpi-label">{label}</div><div class="kpi-sub">{sub}</div></div>')

    cards = (
        '<div class="kpi-row">'
        + _card(len(scored), "총 분석 건수", "DART·뉴스 점수 대상", "var(--text)")
        + _card(s_cnt, "S급 (80점+)", "즉시 영업 대상", "#e8453c")
        + _card(a_cnt, "A급 (60~79)", "선제 접촉 권장", "#f59e0b")
        + _card(invest_label, "S·A급 총 투자", "추정 합산", "var(--link)")
        + '</div>'
    )

    # TOP10 은 영업 우선순위 리더보드 — C급(노이즈·준공·경쟁사 동향)은 제외하고 S/A/B만.
    top = [x for x in scored if x["grade"] != "C"][:10]
    rows = ""
    for i, x in enumerate(top, 1):
        color = _GRADE_COLORS.get(x["grade"], "#888")
        proj = (f'<div style="font-size:11.5px;color:var(--muted);">{_esc(_preview(x["proj"], 46))}</div>'
                if x["proj"] else "")
        amt = _format_won(x["amount"]) if x["amount"] else '<span style="color:var(--muted);">미공시</span>'
        if x["url"]:
            corp_html = f'<a href="{_esc(x["url"])}" target="_blank" style="color:inherit;text-decoration:none;"><b>{_esc(x["corp"])}</b></a>'
        else:
            corp_html = f'<b>{_esc(x["corp"])}</b>'
        rows += (
            f'<tr><td class="num-col">{i}</td>'
            f'<td>{corp_html}{proj}</td>'
            f'<td style="white-space:nowrap;"><span class="src-badge">{_esc(x["src"])}</span></td>'
            f'<td>{_render_chips(x["cats"])}</td>'
            f'<td style="text-align:right;white-space:nowrap;font-weight:600;">{amt}</td>'
            f'<td style="text-align:center;white-space:nowrap;">'
            f'<span style="font-weight:800;color:{color};font-size:16px;">{x["score"]}</span>'
            f'<div style="font-size:10px;font-weight:700;color:{color};">{x["grade"]}</div></td></tr>'
        )

    table = (
        '<div class="top10-wrap"><h2 class="top10-title">🎯 TOP 10 프로젝트 스코어링</h2>'
        '<table class="top10-table"><thead><tr>'
        '<th>#</th><th>발주처 / 프로젝트</th><th>신호 출처</th><th>시설 유형</th>'
        '<th style="text-align:right;">투자 규모</th><th style="text-align:center;">점수</th>'
        '</tr></thead><tbody>' + rows + '</tbody></table></div>'
    )
    return f'<div class="dashboard">{cards}{table}</div>'


def main():
    ap = argparse.ArgumentParser()
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    ap.add_argument("--rss", default=f"data/filtered/{today_str}.jsonl",
                    help="RSS 필터 결과 JSONL (기본: 오늘 날짜)")
    ap.add_argument("--g2b", default=f"data/raw/g2b_{today_str}.jsonl",
                    help="G2B 입찰공고 JSONL (기본: 오늘 날짜)")
    ap.add_argument("--dart", default=f"data/raw/dart_{today_str}.jsonl")
    ap.add_argument("--mfds", default=f"data/raw/mfds_gmp_{today_str}.jsonl",
                    help="MFDS GMP 적합판정 JSONL 경로 (기본: 오늘 날짜)")
    ap.add_argument("--mfds-card-limit", type=int, default=50,
                    help="MFDS 카드 표시 최대 '회사' 수 (회사별 그룹화). 기본 50")
    ap.add_argument("--mfds-recent-days", type=int, default=0,
                    help="MFDS 카드: 발급추정일 (유효기간 - 3년) 기준 최근 N일 이내만. "
                         "0(기본) = 필터 끔(매출 매칭된 모든 회사 카드 표시) — 화면 상단 기간 버튼으로 동적 필터. "
                         "7일 이내는 🆕 NEW 강조.")
    ap.add_argument("--period-days", type=int, default=7,
                    help="리포트 기간 (일) — subtitle/빈섹션 표시용. 기본 7일.")
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    # 경로 정규화: 상대경로면 프로젝트 루트 기준. 이렇게 안 하면 cwd 가 다른 곳일 때
    # 엉뚱한 위치(예: sujoo_radar/data/...)에 빈 파일 생성됨.
    def _resolve(p: str) -> Path:
        pp = Path(p)
        return pp if pp.is_absolute() else (_PROJECT_ROOT / pp)

    rss_items = _read_jsonl(_resolve(args.rss))
    # 공시일(published_at) 내림차순 — HIGH/MID/LOW 모두 최신순으로 표시 (날짜 컬럼 정렬용)
    rss_items.sort(key=lambda it: (it.get("published_at") or ""), reverse=True)
    g2b_items_all = _read_jsonl(_resolve(args.g2b))
    # G2B — 추정가격 1억 미만은 소액이라 컷. 단 0원(=추정가 미공개)은 실제 금액 미상이므로 통과.
    def _g2b_price(it: dict) -> int:
        return _extract_g2b_fields(it.get("content") or "").get("price", 0)
    g2b_items = [it for it in g2b_items_all
                 if _g2b_price(it) == 0 or _g2b_price(it) >= G2B_MIN_PRICE]
    # 추정가격 큰 순 정렬 (0원·미공개는 맨 아래)
    g2b_items.sort(key=_g2b_price, reverse=True)
    g2b_cut = len(g2b_items_all) - len(g2b_items)
    dart_items = _read_jsonl(_resolve(args.dart))
    # 세움터(EAIS) 제거 — 영업팀 요청(2026-06-02). 데이터 미수집 → 빈 리스트 고정.
    eais_items: list[dict] = []
    mfds_items = _read_jsonl(_resolve(args.mfds))
    # MFDS — 유효기간 늦은 순 정렬 (= GMP 3년 가정 시 최근 발급 추정 우선)
    mfds_items.sort(key=lambda x: x.get("vld", ""), reverse=True)
    # ── 카드 필터 ───────────────────────────────────────────
    # 진실: 식약처 OpenAPI 응답에 '발급일자' 필드 없음 — 유효기간(vld)만 제공.
    # GMP 유효기간 3년 가정으로 issued_est = vld - 3y 역산 (mfds_gmp.py 의 to_article).
    #
    # 1) 매출 매칭 (DART 매출 3,000억+ 또는 CDMO/바이오)
    # 2) 발급추정일 ≤ N일 이내 (영업: 진짜 최근 발급된 GMP 만 보고 싶음)
    # ───────────────────────────────────────────────────────
    today_d = datetime.now(KST).date()
    recent_cutoff = today_d - timedelta(days=args.mfds_recent_days)

    def _issued_est_date(it: dict):
        s = (it.get("issued_est") or "").strip()
        if len(s) >= 10:
            try:
                return datetime.strptime(s[:10], "%Y-%m-%d").date()
            except ValueError:
                return None
        return None

    def _is_recent(it: dict) -> bool:
        if args.mfds_recent_days <= 0:
            return True
        d = _issued_est_date(it)
        return d is not None and d >= recent_cutoff

    mfds_card_pool_all_major = [it for it in mfds_items if _is_major_pharma(it.get("bssh", ""))]
    mfds_card_pool = [it for it in mfds_card_pool_all_major if _is_recent(it)]
    # 매출 큰 순 정렬 (영업 우선순위 = 발주 여력 큰 회사)
    def _rev_of(it):
        m = _match_pharma_company(it.get("bssh", ""))
        return m[1].get("revenue", 0) if m else 0
    mfds_card_pool.sort(key=_rev_of, reverse=True)
    # 회사별 그룹화 → 카드 1장 = 1개 회사 (GMP N건).
    _all_groups = _group_mfds_by_company(mfds_card_pool)

    # 🆕 NEW (7일 이내) 회사 먼저 → 그 다음 매출 desc (그룹 내부는 이미 매출 정렬됨)
    def _group_has_new(g) -> bool:
        _, gitems = g
        for it in gitems:
            s = (it.get("issued_est") or "").strip()
            if len(s) >= 10:
                try:
                    d = datetime.strptime(s[:10], "%Y-%m-%d").date()
                    if 0 <= (today_d - d).days <= 7:
                        return True
                except ValueError:
                    pass
        return False

    # 🆕 NEW(7일) 회사 최상단 → 그 안에서 발주가능성 점수(매출·최근GMP·적합도) 내림차순.
    _all_groups.sort(
        key=lambda g: (_group_has_new(g), _mfds_score(g[1])["score"]), reverse=True,
    )
    mfds_groups = _all_groups[: args.mfds_card_limit]
    mfds_card_count = sum(len(items) for _, items in mfds_groups)
    mfds_new_count = sum(1 for g in mfds_groups if _group_has_new(g))
    # 매출 매칭은 됐지만 recent 필터에서 빠진 건수 (헤더에 투명하게 노출)
    mfds_recent_cut = len(mfds_card_pool_all_major) - len(mfds_card_pool)

    # === 기간 계산 ===
    today_dt = datetime.now(KST)
    period_start_dt = today_dt - timedelta(days=args.period_days)
    period_start_str = period_start_dt.strftime("%Y-%m-%d")
    period_end_str = today_str
    period_label = f"{_mmdd(period_start_str)} ~ {_mmdd(period_end_str)}"
    period_label_full = f"{period_start_str} ~ {period_end_str}"

    # 데이터 실제 published_at 범위 (참고용 — 빈 데이터 케이스 대비 안전망)
    actual_range = _date_range_md([rss_items, g2b_items, dart_items, eais_items, mfds_items])
    if actual_range:
        actual_label = f"{_mmdd(actual_range[0])} ~ {_mmdd(actual_range[1])}"
    else:
        actual_label = period_label

    # 분류(classify_rss)는 base 출처(신호 강도)만 정하고, 노출 섹션은 '최종 발주가능성 등급'이
    # 결정한다 (2026-06-05). 이전엔 섹션=classify, 점수=score_opportunity 가 따로 놀아
    # "HIGH인데 C점", "LOW인데 A점" 모순이 났고, 진짜 리드(에어리퀴드 등)가 MID/LOW에 묻혔다.
    # 이제 S/A→HIGH, B→MID, C→LOW 로 섹션과 등급을 일치시킨다.
    rss_scored = []  # (reason, it, source, score_dict)
    for it in rss_items:
        lab, rea = classify_rss(it)
        # 영업 범위 밖(조선/해양·비시설 노이즈)이면 base 를 news_mid 로 낮춤 (HIGH base 박탈)
        if lab == "HIGH":
            oos = _news_out_of_scope(it)
            if oos:
                lab, rea = "MID", f"HIGH강등 · {oos}"
        source = _news_source_for(lab)
        rss_scored.append((rea, it, source, _news_score(it, source)))
    rss_high = [(r, it, s) for r, it, s, sc in rss_scored if sc["grade"] in ("S", "A")]
    rss_mid = [(r, it, s) for r, it, s, sc in rss_scored if sc["grade"] == "B"]
    rss_low = [(r, it, s) for r, it, s, sc in rss_scored if sc["grade"] == "C"]
    # 각 섹션 내부 — 발주가능성 점수 내림차순 (영업 우선순위 노출).
    # 동점은 stable sort 라 직전 published_at 내림차순(최신 먼저)이 유지됨.
    rss_high.sort(key=lambda ri: _news_score(ri[1], ri[2])["score"], reverse=True)
    rss_mid.sort(key=lambda ri: _news_score(ri[1], ri[2])["score"], reverse=True)
    rss_low.sort(key=lambda ri: _news_score(ri[1], ri[2])["score"], reverse=True)

    # DART 1차 — 신규시설투자등 중 건설 영업 대상만. 선박/항공기/엔진 등 동산 자산 취득은 컷.
    # + 시설투자/자산취득 '철회' 정정공시는 영업 가치 없음 → 별도 컷.
    dart_primary_all = [it for it in dart_items if _is_primary_signal(it)]
    dart_primary_construction = [it for it in dart_primary_all if _is_construction_relevant(it)]
    dart_primary = [it for it in dart_primary_construction if not _is_dart_withdrawn(it)]
    dart_primary_cut_asset = len(dart_primary_all) - len(dart_primary_construction)
    dart_primary_cut_withdrawn = len(dart_primary_construction) - len(dart_primary)
    dart_primary_cut = dart_primary_cut_asset + dart_primary_cut_withdrawn
    # 공시 유형별 분리 — 신규시설투자등 vs 유형자산 취득결정
    dart_invest_items = [it for it in dart_primary if not _is_dart_asset_acquisition(it)]
    dart_asset_items = [it for it in dart_primary if _is_dart_asset_acquisition(it)]
    # 신규시설투자: 신규 vs 정정 분리 → 투자금액 내림차순
    dart_primary_new = sorted(
        [it for it in dart_invest_items if not _is_dart_correction(it)],
        key=lambda it: (_dart_item_score(it)["score"], _dart_invest_amount(it)), reverse=True,
    )
    dart_primary_corr = sorted(
        [it for it in dart_invest_items if _is_dart_correction(it)],
        key=lambda it: (_dart_item_score(it)["score"], _dart_invest_amount(it)), reverse=True,
    )
    # 유형자산 취득결정: 한 그룹으로 (신규+정정 통합), 점수 → 취득금액 내림차순
    dart_primary_asset = sorted(
        dart_asset_items,
        key=lambda it: (_dart_item_score(it)["score"], _dart_asset_amount(it)), reverse=True,
    )
    # DART 2차 (공급계약체결) — 영업 가치 있으려면 계약금액 500억+. 그 이하는 협력사·하청 소액으로 노이즈.
    dart_secondary_all = [it for it in dart_items if not _is_primary_signal(it)]
    dart_secondary = [it for it in dart_secondary_all
                      if _extract_dart_contract_amount(it) >= DART_CONTRACT_THRESHOLD]
    # 계약금액 큰 순 정렬 (영업 우선순위)
    dart_secondary.sort(
        key=lambda it: (_dart2_score(it)["score"], _extract_dart_contract_amount(it)),
        reverse=True,
    )
    dart_secondary_cut = len(dart_secondary_all) - len(dart_secondary)
    # 신규 vs 정정 분리 → 그룹별 금액 내림차순 (DART 1차 패턴과 동일)
    dart_secondary_new = [it for it in dart_secondary if not _is_dart_correction(it)]
    dart_secondary_corr = [it for it in dart_secondary if _is_dart_correction(it)]

    total = len(rss_items) + len(g2b_items) + len(dart_items) + len(eais_items) + len(mfds_items)

    # 카테고리별 카운트 (전체 데이터 대상, 멀티태깅이므로 합계 > total 가능)
    cat_counts: Counter[str] = Counter()
    all_items_for_cat = (
        g2b_items + eais_items + dart_primary + dart_secondary + mfds_items
        + [it for _, it, _ in rss_high] + [it for _, it, _ in rss_mid] + [it for _, it, _ in rss_low]
    )
    for it in all_items_for_cat:
        for c in _cats_for(it):
            cat_counts[c] += 1

    out = _resolve(args.output) if args.output else _resolve(f"data/daily_report_{today_str}.html")

    parts = []
    parts.append(f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<title>자이씨앤에이 수주레이더 — {today_str}</title>
<style>{CSS}</style>
</head><body class="theme-dark font-normal">

<!-- 플로팅 컨트롤 (왼쪽 하단): 테마 3개 + 폰트 3개 -->
<div class="float-controls" aria-label="화면 설정">
  <div class="group">
    <span class="lbl">테마</span>
    <button data-theme="theme-dark" title="다크 모드">🌙 다크</button>
    <button data-theme="theme-light" title="라이트 모드">☀️ 라이트</button>
    <button data-theme="theme-sepia" title="블루라이트 차단">📜 세피아</button>
  </div>
  <div class="group">
    <span class="lbl">글씨</span>
    <button data-font="font-normal" title="보통 크기" style="font-size:11px;">가</button>
    <button data-font="font-large" title="약간 큰" style="font-size:13.5px;">가</button>
    <button data-font="font-xlarge" title="확실히 큰" style="font-size:16px;">가</button>
  </div>
</div>

<!-- 플로팅 목차 (오른쪽) — 숫자 번호 · DART 2차 이하는 [참조] -->
<nav class="float-toc" aria-label="목차">
  <div class="toc-title">목차</div>
  <a href="#favorites" data-toc="favorites" class="toc-pin">중요정보 <span class="cnt" id="fav-count-toc">0</span></a>
  <a href="#dart1" data-toc="dart1" class="toc-primary sec-color-dart1">1. DART 1차 <span class="cnt">{len(dart_primary)}</span></a>
  <a href="#rss-high" data-toc="rss-high" class="toc-primary sec-color-rss-high">2. 뉴스 HIGH <span class="cnt">{len(rss_high)}</span></a>
  <a href="#mfds" data-toc="mfds" class="toc-primary sec-color-mfds">3. 식약처 GMP <span class="cnt">{len(mfds_items)}</span></a>
  <div class="toc-sep">참조</div>
  <a href="#dart2" data-toc="dart2" class="toc-ref sec-color-dart2">4. DART 2차 <span class="cnt">{len(dart_secondary)}</span></a>
  <a href="#rss-mid" data-toc="rss-mid" class="toc-ref sec-color-rss-mid">5. 뉴스 MID <span class="cnt">{len(rss_mid)}</span></a>
  <a href="#rss-low" data-toc="rss-low" class="toc-ref sec-color-rss-low">6. 뉴스 LOW <span class="cnt">{len(rss_low)}</span></a>
  <a href="#g2b" data-toc="g2b" class="toc-ref sec-color-g2b">7. 나라장터 <span class="cnt">{len(g2b_items)}</span></a>
</nav>

<div class="container">
  <h1>자이씨앤에이 수주레이더</h1>
  <div class="subtitle">
    🗓️ <strong>{period_label_full}</strong> (최근 {args.period_days}일) ·
    생성: {today_str} · 총 <strong>{total}건</strong>의 알파 시그널
  </div>

  <!-- 통합 sticky zone: 섹션 nav + 카테고리 필터 + 검색이 함께 따라다님 -->
  <div class="sticky-zone">
    <div class="nav">
      <a href="#favorites" class="toc-pin">중요정보 <span id="fav-count-nav">0</span></a>
      <a href="#dart1" class="toc-primary sec-color-dart1">1. DART 1차 {len(dart_primary)}</a>
      <a href="#rss-high" class="toc-primary sec-color-rss-high">2. 뉴스 HIGH {len(rss_high)}</a>
      <a href="#mfds" class="toc-primary sec-color-mfds">3. 식약처 GMP {len(mfds_items)}</a>
      <span class="nav-sep">참조</span>
      <a href="#dart2" class="toc-ref sec-color-dart2">4. DART 2차 {len(dart_secondary)}</a>
      <a href="#rss-mid" class="toc-ref sec-color-rss-mid">5. 뉴스 MID {len(rss_mid)}</a>
      <a href="#rss-low" class="toc-ref sec-color-rss-low">6. 뉴스 LOW {len(rss_low)}</a>
      <a href="#g2b" class="toc-ref sec-color-g2b">7. 나라장터 {len(g2b_items)}</a>
    </div>

    <div class="catbar">
      <span class="catbar-label">🏷️ 카테고리</span>
      <span class="catbtn" data-cat="">전체 <span class="cnt">{len(all_items_for_cat)}</span></span>
""")
    for cat in CATEGORY_ORDER:
        cnt = cat_counts.get(cat, 0)
        color = CATEGORY_COLORS.get(cat, "#5a6473")
        zero_cls = " zero" if cnt == 0 else ""
        # 0건도 표시 (zero 클래스로 흐리게). 클릭하면 빈 상태 placeholder 가 뜸.
        parts.append(
            f'      <span class="catbtn{zero_cls}" data-cat="{html.escape(cat)}" '
            f'style="border-left: 4px solid {color};">'
            f'{html.escape(cat)} <span class="cnt">{cnt}</span></span>\n'
        )
    parts.append("""    </div>

    <div class="filter">
      <div class="filter-input-wrap">
        <input id="q" type="text" placeholder="🔍 키워드 검색 (회사명/지역/공장/클린룸/반도체 등)" />
        <button type="button" class="clear-search" id="q-clear" title="검색어 지우기 (Esc)" aria-label="검색어 지우기">✕</button>
      </div>
    </div>
  </div>  <!-- /.sticky-zone -->

  <p style="color:var(--muted); font-size:13px;">
    💡 영업 우선순위: <strong>1.DART 1차 → 2.뉴스 HIGH → 3.식약처</strong> · <span style="color:var(--muted-soft);">[참조] 4.DART 2차 · 5.뉴스 MID · 6.뉴스 LOW · 7.나라장터(관급) — 영업 직접 대상 아니라 기본 접힘</span>.<br/>
    🏷️ 카테고리 칩을 클릭하면 해당 시설 유형만 필터링됩니다 · 각 행 왼쪽 <strong>☆ 별</strong>을 누르면 맨 위 <strong>⭐ 중요정보</strong>로 고정됩니다.
  </p>
""")

    # === Phase 3: 상단 대시보드 — 전 섹션 점수 통합 KPI 카드 + TOP10 스코어링 ===
    _scored = _collect_scored(dart_invest_items, dart_asset_items, rss_high, dart_secondary)
    parts.append(render_dashboard(_scored))

    # === ⭐ 중요정보 (즐겨찾기) — 맨 위 고정 패널. JS 가 별 토글마다 채움 ===
    parts.append("""
  <section id="favorites" class="favorites-pinned">
    <h2>⭐ 중요정보 (즐겨찾기)</h2>
    <div class="fav-hint">각 행 왼쪽의 ☆ 별을 누르면 여기에 모입니다 · 브라우저에 저장돼 다음 주 리포트에서도 유지됩니다 · ✕ 로 해제 · ↓본문 으로 점프.</div>
    <div id="fav-list"></div>
  </section>
""")

    def _placeholder(sec_id: str, what: str, is_originally_empty: bool) -> str:
        """섹션 빈 상태 div. 원래 0건이면 즉시 표시, 아니면 hidden(JS 필터용)."""
        if is_originally_empty:
            text = f'📭 <strong>{html.escape(period_label_full)}</strong> 기간 동안 수집된 {html.escape(what)} 없음'
            return (f'<div class="empty-state" id="empty-{sec_id}" data-original="1">{text}</div>')
        else:
            text = f'🔍 현재 필터 조건에 맞는 {html.escape(what)} 없음 — 카테고리/검색어를 바꿔보세요'
            return (f'<div class="empty-state" id="empty-{sec_id}" data-original="0" style="display:none;">{text}</div>')

    # === G2B 섹션은 페이지 맨 아래로 이동됨 (관급 — 영업 대상 아님, 기본 접힘) ===
    # → RSS LOW 다음, Footer 직전에 렌더링됨.
    g2b_cut_label = f", 1억 미만 {g2b_cut}건 컷" if g2b_cut else ""

    # === DART 1차 === (세움터/EAIS 섹션은 영업팀 요청으로 제거됨 2026-06-02)
    cut_bits: list[str] = []
    if dart_primary_cut_asset:
        cut_bits.append(f"동산자산 {dart_primary_cut_asset}건")
    if dart_primary_cut_withdrawn:
        cut_bits.append(f"철회 {dart_primary_cut_withdrawn}건")
    dart_primary_cut_label = f", {' · '.join(cut_bits)} 컷" if cut_bits else ""
    parts.append(f"""
  <h2 id="dart1" data-section="dart1">⭐⭐⭐ 1. DART 1차 신호 — 시설투자 결정 ({len(dart_primary)}건{dart_primary_cut_label})<span class="visible-count" style="color:var(--muted);font-size:13px;font-weight:400;"></span></h2>
  <p style="color:var(--muted);">발주처가 "시설 짓겠다" 결정. 시공사 미정 — 공시 후 3~6개월 내 발주 예정. <b>동산 자산 취득(선박·항공기·엔진) 및 시설투자/자산취득 '철회'는 영업 노이즈로 컷.</b> 신규/정정 분리, 그룹 내 투자금액 큰 순. <b>500억 이상 강조</b>.</p>
""")
    parts.append(_placeholder("dart1", "DART 1차 시설투자 공시", len(dart_primary) == 0))
    if dart_primary:
        parts.append("""  <table>
    <thead><tr><th>#</th><th title="발주가능성 점수 — S 80+ / A 60+ / B 40+ / C">점수</th><th>회사</th><th>구분</th><th style="text-align:right;">투자금액</th><th>투자목적</th><th>투자기간</th><th>공시일</th></tr></thead>
    <tbody>
""")
        # 신규시설투자등 — 신규 그룹 (최상단)
        if dart_primary_new:
            parts.append(
                f'<tr class="group-header" data-group="dart1-new"><td colspan="8"'
                f'style="background:var(--group-new-bg);font-weight:600;color:var(--group-new-fg);'
                f'padding:10px 12px;border-top:2px solid var(--accent-good);">'
                f'🆕 신규시설투자 — 신규 ({len(dart_primary_new)}건) · 발주 임박, 영업 1순위</td></tr>\n'
            )
            for i, it in enumerate(dart_primary_new, 1):
                parts.append(render_dart_primary_card(it, i, group="dart1-new"))
        # 신규시설투자등 — 정정 그룹 (중간)
        if dart_primary_corr:
            base = len(dart_primary_new)
            parts.append(
                f'<tr class="group-header" data-group="dart1-corr"><td colspan="8"'
                f'style="background:var(--group-corr-bg);font-weight:600;color:var(--group-corr-fg);'
                f'padding:10px 12px;border-top:2px solid var(--accent-warn);">'
                f'✏️ 신규시설투자 — 정정 ({len(dart_primary_corr)}건) · 기존 공시 변경, 참고 자료</td></tr>\n'
            )
            for i, it in enumerate(dart_primary_corr, 1):
                parts.append(render_dart_primary_card(it, base + i, group="dart1-corr"))
        # 유형자산 취득결정 그룹 (최하단) — 별도 본문 구조, 같은 7컬럼 의미 매핑
        if dart_primary_asset:
            base = len(dart_primary_new) + len(dart_primary_corr)
            parts.append(
                f'<tr class="group-header" data-group="dart1-asset"><td colspan="8"'
                f'style="background:var(--info-bg);font-weight:600;color:var(--info-fg);'
                f'padding:10px 12px;border-top:2px solid var(--info-fg);">'
                f'🏛️ 유형자산 취득결정 ({len(dart_primary_asset)}건) · 토지·건물·부동산 매입 — 신축/이전 잠재 '
                f'<span style="font-weight:400;font-size:11.5px;opacity:0.85;">'
                f'· 컬럼 의미: 구분=취득목적물 / 금액=취득금액(자산%) / 목적=📍거래상대방+취득목적 / 기간=취득예정일</span>'
                f'</td></tr>\n'
            )
            for i, it in enumerate(dart_primary_asset, 1):
                parts.append(render_dart_asset_card(it, base + i, group="dart1-asset"))
        parts.append("    </tbody></table>")

    # === RSS HIGH ===
    parts.append(f"""
  <h2 id="rss-high" data-section="rss-high">🟢 2. 뉴스 HIGH ({len(rss_high)}건)<span class="visible-count" style="color:var(--muted);font-size:13px;font-weight:400;"></span></h2>
  <p style="color:var(--muted);">강한 action(착공/신축/증설/수주) + 강한 target(공장/플랜트/클린룸). 본문 확인 필요.</p>
""")
    parts.append(_placeholder("rss-high", "뉴스 HIGH", len(rss_high) == 0))
    if rss_high:
        parts.append("""  <table>
    <thead><tr><th>#</th><th title="발주가능성 점수 — S 80+ / A 60+ / B 40+ / C">점수</th><th>매체</th><th>제목 / 본문</th><th>매칭 키워드</th><th>게시일</th></tr></thead>
    <tbody>
""")
        for i, (reason, it, source) in enumerate(rss_high, 1):
            parts.append(render_rss_high_card(reason, it, i, source))
        parts.append("    </tbody></table>")

    # === MFDS GMP (DART 2차 위로 이동 — 사용자 우선순위 조정) ===
    parts.append(f"""
  <h2 id="mfds" data-section="mfds">💊 3. 식약처 의약품 GMP 적합판정 — 매출 50위 + CDMO/바이오 ({len(mfds_groups)}개사{f', <span style="color:var(--accent-good);font-weight:700;">🆕 NEW {mfds_new_count}개사</span>' if mfds_new_count else ''}, GMP {mfds_card_count}건 / 전체 {len(mfds_items)}건 中)<span class="visible-count" style="color:var(--muted);font-size:13px;font-weight:400;"></span></h2>
  <p style="color:var(--muted);"><b>데이터 수집일: {today_str}</b> · 발급추정일은 <b>유효기간(vld) − 3년</b> 역산 (GMP 적합판정 유효기간 3년 가정). 식약처 OpenAPI 응답에 발급일자 필드 자체가 없음 — 정확도 한계. 의약품안전나라의 "허가일자" 는 의약품 품목허가일이라 GMP 발급일과 별개.<br/><br/>
  <b>표시 규칙</b>: 🆕 NEW (7일 이내) → 녹색 강조 · 30일 이내 → "{_quarter_label(today_d)} · N일 전" · 그 이상은 분기·개월수 표시. NEW 회사를 최상단 배치.<br/>
  <b>필터</b>: 매출 매칭 (3,000억+ 또는 CDMO/바이오 — 500억+ 공장 발주 여력) 통과 회사 전체 표시. <b>발주가능성 점수순</b> 정렬 (매출·최근 GMP·제약바이오 적합도 / 🆕 NEW 7일 이내 최상단). <b>아래 기간 버튼</b>으로 발급추정일 기준 좁히기 (행 단위 동적 필터). <b>🏛️ 식약처 의약품안전나라</b> 링크는 회사 허가 의약품 라인업 (참고용).</p>
""")
    parts.append(_placeholder("mfds", "식약처 GMP 적합판정", len(mfds_items) == 0))
    if mfds_items:
        parts.append(render_mfds_stats(mfds_items))
        # === MFDS 전용 기간 필터 — 발급추정일 기준 ===
        # data-mfds-min-days 회사 그룹 단위로 부여. JS applyFilter 가 기간 체크.
        parts.append("""
  <div class="mfds-period-bar" id="mfds-period-bar">
    <span class="mfds-period-label">📅 발급추정일 기간:</span>
    <button class="mfds-period-btn active" type="button" data-mfds-period="0">전체</button>
    <button class="mfds-period-btn" type="button" data-mfds-period="7">7일</button>
    <button class="mfds-period-btn" type="button" data-mfds-period="30">30일</button>
    <button class="mfds-period-btn" type="button" data-mfds-period="90">90일</button>
    <button class="mfds-period-btn" type="button" data-mfds-period="180">180일</button>
    <button class="mfds-period-btn" type="button" data-mfds-period="365">1년</button>
  </div>
""")
    if mfds_groups:
        parts.append("""  <table>
    <thead><tr>
      <th>#</th>
      <th title="발주가능성 점수 — 매출(발주 여력)·최근 GMP·제약바이오 적합도. S 80+ / A 60+ / B 40+ / C">점수</th>
      <th>회사</th>
      <th>구분</th>
      <th>제형</th>
      <th>발급추정일</th>
      <th>유효기간</th>
      <th>공장 소재지</th>
      <th>링크</th>
    </tr></thead>
    <tbody>
""")
        for i, (_, group_items) in enumerate(mfds_groups, 1):
            parts.append(render_mfds_company_row(group_items, i, today_d=today_d))
        parts.append("    </tbody></table>")

    # === DART 2차 (참조 — 시공사 이미 확정된 케이스, 영업 직접 대상 아님. 기본 접힘) ===
    parts.append(f"""
  <h2 id="dart2" data-section="dart2" data-collapsible="1">⭐⭐ 4. [참조] DART 2차 정보 — 시공사 공급계약체결 500억+ ({len(dart_secondary)}건, 500억 미만 {dart_secondary_cut}건 컷)<span class="visible-count" style="color:var(--muted);font-size:13px;font-weight:400;"></span></h2>
  <p style="color:var(--muted);">이미 시공사 결정된 케이스. 협력사·하청 영업, 경쟁사 동향, 발주처-시공사 매칭 파악용. <b>계약금액 500억 이상만, 큰 순 정렬, 신규/정정 분리</b>. 본문 "계약상대방·공급지역·계약기간" 본문 파싱해서 열로 분리.</p>
""")
    parts.append(_placeholder("dart2", "DART 2차 공급계약 공시", len(dart_secondary) == 0))
    if dart_secondary:
        parts.append("""  <table>
    <thead><tr><th>#</th><th title="발주가능성 점수 — S 80+ / A 60+ / B 40+ / C">점수</th><th>회사</th><th>계약명</th><th>발주처</th><th>지역</th><th style="text-align:right;">계약금액</th><th>계약기간</th><th>공시일</th></tr></thead>
    <tbody>
""")
        # 신규 그룹 (위)
        if dart_secondary_new:
            parts.append(
                f'<tr class="group-header" data-group="dart2-new"><td colspan="9"'
                f'style="background:var(--group-new-bg);font-weight:600;color:var(--group-new-fg);'
                f'padding:10px 12px;border-top:2px solid var(--accent-good);">'
                f'🆕 신규 공급계약 ({len(dart_secondary_new)}건) · 시공사 확정·발주처 식별</td></tr>\n'
            )
            for i, it in enumerate(dart_secondary_new, 1):
                parts.append(render_dart_secondary_row(it, i, group="dart2-new"))
        # 정정 그룹 (아래)
        if dart_secondary_corr:
            base = len(dart_secondary_new)
            parts.append(
                f'<tr class="group-header" data-group="dart2-corr"><td colspan="9"'
                f'style="background:var(--group-corr-bg);font-weight:600;color:var(--group-corr-fg);'
                f'padding:10px 12px;border-top:2px solid var(--accent-warn);">'
                f'✏️ 정정 공급계약 ({len(dart_secondary_corr)}건) · 기존 계약 금액·기간 변경</td></tr>\n'
            )
            for i, it in enumerate(dart_secondary_corr, 1):
                parts.append(render_dart_secondary_row(it, base + i, group="dart2-corr"))
        parts.append("    </tbody></table>")

    # === RSS MID ===
    parts.append(f"""
  <h2 id="rss-mid" data-section="rss-mid" data-collapsible="1">🟡 5. [참조] 뉴스 MID ({len(rss_mid)}건)<span class="visible-count" style="color:var(--muted);font-size:13px;font-weight:400;"></span></h2>
  <p style="color:var(--muted);">일부 조건만 만족. 제목으로 빠른 스캔. <b>발주가능성 점수순</b> 정렬.</p>
""")
    parts.append(_placeholder("rss-mid", "뉴스 MID", len(rss_mid) == 0))
    if rss_mid:
        parts.append("""  <table>
    <thead><tr><th>#</th><th title="발주가능성 점수 — S 80+ / A 60+ / B 40+ / C">점수</th><th>매체</th><th>제목</th><th>매칭 키워드</th><th>게시일</th></tr></thead>
    <tbody>
""")
        for i, (reason, it, source) in enumerate(rss_mid, 1):
            parts.append(render_rss_mid_row(reason, it, i, source))
        parts.append("    </tbody></table>")

    # === RSS LOW ===
    parts.append(f"""
  <h2 id="rss-low" data-section="rss-low" data-collapsible="1">🔴 6. [참조] 뉴스 LOW ({len(rss_low)}건)<span class="visible-count" style="color:var(--muted);font-size:13px;font-weight:400;"></span></h2>
  <p style="color:var(--muted);">노이즈 가능성. 5초 안에 패스. <b>발주가능성 점수순</b> 정렬.</p>
""")
    parts.append(_placeholder("rss-low", "뉴스 LOW", len(rss_low) == 0))
    if rss_low:
        parts.append("""  <table>
    <thead><tr><th>#</th><th title="발주가능성 점수 — S 80+ / A 60+ / B 40+ / C">점수</th><th>매체</th><th>제목</th><th>매칭 키워드</th><th>게시일</th></tr></thead>
    <tbody>
""")
        for i, (reason, it, source) in enumerate(rss_low, 1):
            parts.append(render_rss_low_row(reason, it, i, source))
        parts.append("    </tbody></table>")

    # === G2B (관급 — 영업 대상 아님, 기본 접힘. 페이지 맨 아래로 배치) ===
    parts.append(f"""
  <h2 id="g2b" data-section="g2b" data-collapsible="1">⭐ 7. [참조] 나라장터 입찰공고 (관급 — 영업 대상 아님) ({len(g2b_items)}건{g2b_cut_label})<span class="visible-count" style="color:var(--muted);font-size:13px;font-weight:400;"></span></h2>
  <p style="color:var(--muted);">공공/관급 시설공사 입찰. 자이씨앤에이는 관급 대상이 아니므로 참고용. 기본 접힘 — 필요 시 헤더 클릭하면 펼쳐짐.</p>
""")
    parts.append(_placeholder("g2b", "나라장터 입찰공고", len(g2b_items) == 0))
    if g2b_items:
        parts.append("""  <table>
    <thead><tr><th>#</th><th>사업명</th><th>발주처</th><th style="text-align:right;">추정가격<br/><span style="font-size:10px;color:var(--muted);font-weight:400;">(VAT 제외)</span></th><th>개찰일시</th><th>공시일</th></tr></thead>
    <tbody>
""")
        for i, it in enumerate(g2b_items, 1):
            parts.append(render_g2b_card(it, i))
        parts.append("    </tbody></table>")

    # === Footer ===
    parts.append(f"""
  <div class="footer">
    생성: {datetime.now(KST).strftime('%Y-%m-%d %H:%M')} KST ·
    데이터: 나라장터 + DART OpenAPI + 식약처 GMP + 뉴스 45개 매체
  </div>
</div>
<button class="top-btn" id="topBtn" title="맨 위로" onclick="window.scrollTo({{top:0,behavior:'smooth'}})">↑</button>
<script>{JS}</script>
<script>
  // 스크롤 위치에 따라 top 버튼 보이기/숨기기
  (function() {{
    var btn = document.getElementById('topBtn');
    if (!btn) return;
    function update() {{
      if (window.scrollY > 300) btn.classList.add('visible');
      else btn.classList.remove('visible');
    }}
    window.addEventListener('scroll', update, {{ passive: true }});
    update();
  }})();

  // 목차/nav 앵커 클릭 시 섹션 제목이 sticky 메뉴에 가리지 않게 —
  // sticky-zone 실제 높이를 측정해 scroll-padding-top 으로 반영 (높이 가변 대응).
  (function() {{
    var sticky = document.querySelector('.sticky-zone');
    if (!sticky) return;
    function setPad() {{
      var h = sticky.offsetHeight || 0;
      document.documentElement.style.scrollPaddingTop = (h + 8) + 'px';
    }}
    setPad();
    window.addEventListener('resize', setPad, {{ passive: true }});
    // 폰트 크기 토글 등으로 높이 바뀔 때도 갱신
    if (window.ResizeObserver) {{
      new ResizeObserver(setPad).observe(sticky);
    }}
  }})();
</script>
</body></html>""")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(parts), encoding="utf-8")
    print(f"HTML 리포트 저장: {out}")
    print(f"  ⭐ G2B {len(g2b_items)}  ⭐ DART1 {len(dart_primary)}  "
          f"⭐⭐ DART2 {len(dart_secondary)}  💊 식약처 {len(mfds_items)}  🟢 RSS-HIGH {len(rss_high)}  "
          f"🟡 RSS-MID {len(rss_mid)}  🔴 RSS-LOW {len(rss_low)}")
    print(f"  → 브라우저에서 더블클릭으로 열어보세요: {out.absolute()}")


if __name__ == "__main__":
    main()
