"""Stage 0 (보조): DART 전자공시 수집기.

대기업이 직접 발표하는 시설투자/유형자산 양수 공시 = 가장 빠른 수주 신호.
공시 결정 후 통상 3~6개월 내 발주가 이뤄지므로 영업 골든타임 확보.

인증키 발급:
    https://opendart.fss.or.kr → 인증키 신청·관리 → 이메일 인증
    .env 의 OPENDART_API_KEY 에 추가

사용법:
    python -m src.stage0_collect.dart --days 14
    python -m src.stage0_collect.dart --days 14 --insecure
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import urllib3
from dotenv import load_dotenv

from ..common.io import write_jsonl
from ..common.schema import Article
from .dart_detail import fetch_body

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))

BASE_URL = "https://opendart.fss.or.kr/api"
LIST_ENDPOINT = f"{BASE_URL}/list.json"

# 자이씨앤에이 영업·시장 정보로 유용한 공시
# 두 부류 모두 통과:
#  (1) 발주처 직접 시설투자 = 1차 영업 골든타임 (시공사 미정)
#  (2) 시공사·장비사의 공급계약체결 = 이미 시공사 결정됐어도
#      협력사·하청·경쟁사 동향·시장 정보로 유용
TARGET_REPORT_PATTERNS = [
    "신규시설투자",       # ★ 발주처 본인 시설투자 결정 (1차 신호)
    "유형자산양수",       # ★ 토지·건물 매입 (1차 신호)
    "유형자산취득",       # 유형자산 취득 결정 (구표기)
    "공장신축",
    "공장증설",
    "공장신설",
    "공급계약",           # 시공사·장비사 수주공시 (2차 정보: 시공사 결정됨)
]
# 명시적 제외 패턴 — TARGET 에 부분일치하지만 영업·시장 정보로 가치 없음
EXCLUDE_REPORT_PATTERNS = [
    "타법인주식양수",     # M&A
    "사업양수",
    "주식양수",
    "회사분할",
    "합병",
    "공급계약해지",       # 노이즈 — 수주 취소·해지 (실제 14일 184건 中 4건 발견)
    "계약해지",
]


# 보고서 유형별 우선순위 (영업 가치 순) — Article extra 에 부여.
# 1순위: 발주처 본인의 시설투자 결정 (시공사 미정 골든타임)
# 2순위: 토지·건물 매입 (착공 6~12개월 전 신호)
# 3순위: 시공사/장비사 수주공시 (이미 시공사 결정됨, 시장 정보용)
REPORT_KIND_PRIORITIES: list[tuple[int, str, list[str]]] = [
    (1, "신규시설투자",  ["신규시설투자"]),
    (2, "유형자산취득",  ["유형자산양수", "유형자산취득"]),
    (2, "공장신증설",    ["공장신축", "공장증설", "공장신설"]),
    (3, "공급계약체결",  ["공급계약"]),
]


def classify_report_kind(report_nm: str) -> tuple[int, str]:
    """보고서명 → (우선순위, 종류 라벨). 매칭 없으면 (9, '기타')."""
    name_compact = (report_nm or "").replace(" ", "").replace("ㆍ", "").replace("·", "")
    for prio, label, kws in REPORT_KIND_PRIORITIES:
        if any(kw in name_compact for kw in kws):
            return prio, label
    return 9, "기타"

# 본문 기반 후처리 필터 — "유형자산 취득" 공시의 정밀 분류
# DART 의 "유형자산 취득결정" 공시는 두 종류가 섞임:
#   (A) 토지·건물·공장부지 취득 → ★ 향후 시공 발주 신호 (유지)
#   (B) 기계장치·설비 취득 (예: Teradyne 테스트장비 6대) → 시공 무관 (제외)
# 보고서명만 보면 구분 불가. 본문의 "취득목적물" 필드를 보고 분류.
_ACQUISITION_REPORT_PATTERNS = ["유형자산취득", "유형자산양수"]
EQUIPMENT_TARGET_KEYWORDS = [
    # 기계·전자 장비
    "기계장치", "기계 장치", "설비", "장비", "시스템", "기기",
    "측정기", "테스트장비", "검사장비", "공작기계", "반도체장비",
    "장치 일체", "설비 일체",
    # 운송수단 (시공 무관)
    "선박", "포설선", "운반선", "운송선", "탱커",
    "항공기", "헬기", "차량", "운송수단", "특수차량",
]
REAL_ESTATE_TARGET_KEYWORDS = [
    "토지", "건물", "부지", "사옥", "공장건물", "사업장",
    "건축물", "공장 신축", "공장신축", "공장 부지", "공장부지",
    "사무동", "생산동", "본사 사옥", "판매사옥",
]
# 건설사 관점에서 노이즈인 케이스: "이미 지어진 건물 매입" (시공 기회 0)
# - 취득목적물에 '건물'/'사옥' 등이 포함됨
# - 신축·건립 의도 없음 (단순 매입)
EXISTING_BUILDING_KEYWORDS = [
    "건물", "건축물", "건물전부", "건물 전부",
    "사옥", "판매사옥", "본사사옥", "본사 사옥",
    "오피스빌딩", "오피스 빌딩", "상가건물", "상가 건물",
]
# 신축 의도 시그널 — 이게 있으면 "건물" 매입처럼 보여도 향후 시공 가능성 살림
# (예: "기존 공장 매입 후 증축", "토지·건물 매입 후 신공장 건립")
NEW_BUILD_INTENT_KEYWORDS = [
    "신축", "건립", "건설", "신증설", "증설", "신설", "착공",
    "공장신축", "공장 신축", "신공장", "신규 공장", "신규공장",
    "공장신설", "공장 신설", "증축",
]
# === 비건설 자산 — 시공 무관 ===
# 1) 원자재·귀금속 commodity (효성화학 백금 1,983억 케이스)
#    회사가 산업 commodity 를 비축·투자 목적으로 매입 — 건물/공장 시공 없음.
COMMODITY_TARGET_KEYWORDS = [
    "백금", "금괴", "은괴", "귀금속", "지금(地金)", "지금",
    "구리괴", "동괴", "니켈괴", "알루미늄괴", "리튬", "코발트",
    "원자재", "원료자산", "자원 자산", "비철금속",
]
# 2) 농림업·재배 — 차 재배단지·농장·임야 등. 자이씨앤에이 시공 영역(공장·CR·GMP) 밖.
#    (씨엑스아이 윈난 차 재배단지 859억 케이스)
AGRICULTURE_TARGET_KEYWORDS = [
    "재배단지", "차 재배", "차재배", "차나무",
    "농장", "농원", "농지", "축사", "축산",
    "임야", "임업", "산림", "조림",
    "양식장", "양식 시설", "양어장",
    "과수원", "농작물", "작물 재배",
]
# 3) 관계사간 자산이동 / 임대수익 — 신규 시공 의도 없음.
#    (경동나비엔 1,196억 "생산기지 통합에 따른 관계사간 자산 포트폴리오 조정")
#    (효성화학 백금 "투자 및 임대목적 자산취득" 도 여기서 추가 차단)
INTER_AFFILIATE_PURPOSE_KEYWORDS = [
    "자산 포트폴리오", "자산포트폴리오",
    "관계사간", "관계사 간", "계열사간", "계열사 간",
    "생산기지 통합", "생산 기지 통합", "생산기지통합",
    "그룹 내 자산", "그룹내 자산", "그룹 재편", "그룹내 재편",
    "지배구조 조정", "사업구조 개편", "지분 정리",
    "투자 및 임대목적", "투자및임대목적", "투자 및 임대 목적",
    "임대수익", "임대 수익",
]
# "취득목적물" / "취득물건 구분" / "취득물건명" 필드 모두 추출
# DART 공시 양식이 두 가지 — 하나만 잡으면 50% 놓침
_ACQUISITION_TARGET_RE = __import__("re").compile(
    r"(?:취득목적물|취득물건\s*구분|취득물건명|취득자산)\s*([^\n]{1,80})"
)
# 취득목적(=목적·사유 필드) 추출 — 신축 의도 판정용
_ACQUISITION_PURPOSE_RE = __import__("re").compile(
    r"(?:취득목적|취득사유|취득의\s*목적)\s*([^\n]{1,250})"
)

REQUEST_TIMEOUT = 20
USER_AGENT = "sujoo-radar/0.1 (xicna dart client)"


def _make_id(rcept_no: str) -> str:
    """접수번호 md5 → 멱등 ID."""
    return hashlib.md5(f"dart:{rcept_no}".encode("utf-8")).hexdigest()[:16]


def _matches_target(report_nm: str) -> bool:
    """발주처 직접 시설투자 공시만 True. 시공사·장비업체 공급계약은 False."""
    name_compact = report_nm.replace(" ", "").replace("ㆍ", "").replace("·", "")
    # 1) 명시적 제외 우선
    if any(p in name_compact for p in EXCLUDE_REPORT_PATTERNS):
        return False
    # 2) 타겟 매칭
    return any(p in name_compact for p in TARGET_REPORT_PATTERNS)


def _is_equipment_only_acquisition(title: str, body: str) -> bool:
    """본문 기반 후처리: '기계·장비 취득' 공시만 True (제외 대상).

    "유형자산 취득" 공시 중 시공과 무관한 단순 장비 도입 (예: Teradyne 테스트장비 6대)
    을 골라낸다. 토지·건물 취득은 시공 발주의 선행 신호이므로 유지.

    판정 흐름:
      1. 보고서명에 "유형자산취득"/"유형자산양수" 없으면 → False (검사 안 함)
      2. 본문에서 "취득목적물" 다음 100자 추출
      3. 추출 실패 → False (보수적 유지)
      4. 부동산 키워드 있으면 → False (장비 + 부동산 묶음 매입 가능성, 유지)
      5. 장비 키워드만 있으면 → True (제외)
      6. 둘 다 없으면 → False (모호하면 유지)
    """
    title_compact = (title or "").replace(" ", "").replace("ㆍ", "").replace("·", "")
    if not any(p in title_compact for p in _ACQUISITION_REPORT_PATTERNS):
        return False
    if not body:
        return False
    # findall 로 "취득목적물" + "취득물건 구분" + "취득물건명" 모두 추출 후 합쳐서 검사
    matches = _ACQUISITION_TARGET_RE.findall(body)
    if not matches:
        return False  # 양식 안 맞으면 보수적으로 유지
    target_text = " ".join(matches)
    has_real_estate = any(kw in target_text for kw in REAL_ESTATE_TARGET_KEYWORDS)
    if has_real_estate:
        return False  # 부동산 묶여있으면 살림
    has_equipment = any(kw in target_text for kw in EQUIPMENT_TARGET_KEYWORDS)
    return has_equipment


def _is_existing_building_acquisition(title: str, body: str) -> bool:
    """본문 기반 후처리: '이미 지어진 건물 매입' 공시만 True (제외 대상).

    건설사 관점에서 시공 기회 없는 케이스 컷:
      - 셀바이오휴먼텍 "토지 및 건물전부" 5.4억 매입 → 시공 기회 0
      - 코람코라이프인프라리츠 "현대차 판매사옥 외 10개" 5,230억 매입 → REITs 임대수익
      - 피에스텍/코이즈 "토지 및 건물" 매입 → 이미 지어진 공장 매입

    반대로 살리는 경우 (NEW_BUILD_INTENT 키워드 함께 있음):
      - "기존 공장 매입 후 증축"
      - "토지·건물 매입 후 신공장 건립"
      - "사옥 매입 후 신축"

    판정 흐름:
      1. 보고서명에 "유형자산취득"/"유형자산양수" 없으면 → False (검사 안 함)
      2. 본문에서 취득목적물 추출 — 건물 키워드 없으면 → False (토지만 매입 = 살림)
      3. 취득목적물 + 취득목적 어디에도 신축 의도 없으면 → True (제외)
      4. 신축 의도 있으면 → False (살림)
    """
    title_compact = (title or "").replace(" ", "").replace("ㆍ", "").replace("·", "")
    if not any(p in title_compact for p in _ACQUISITION_REPORT_PATTERNS):
        return False
    if not body:
        return False
    target_matches = _ACQUISITION_TARGET_RE.findall(body)
    if not target_matches:
        return False
    target_text = " ".join(target_matches)
    has_building = any(kw in target_text for kw in EXISTING_BUILDING_KEYWORDS)
    if not has_building:
        return False  # 건물 단어 없음 → 토지만 매입(살림) or 장비(다른 함수가 처리)
    # 건물 단어 있음 → 신축 의도 검사
    purpose_matches = _ACQUISITION_PURPOSE_RE.findall(body)
    combined_text = target_text + " " + " ".join(purpose_matches)
    has_new_build_intent = any(kw in combined_text for kw in NEW_BUILD_INTENT_KEYWORDS)
    return not has_new_build_intent  # 신축 의도 없으면 제외


def _is_noncon_acquisition(title: str, body: str) -> bool:
    """본문 기반 후처리: '비건설 자산' 취득 공시 제외 (시공 발주 0).

    3종 노이즈 컷:
      A) commodity 단독 매입 — 백금·귀금속·원자재 (효성화학 백금 1,983억 케이스)
      B) 농림업 — 차 재배단지·농장·임야 등 (씨엑스아이 윈난 차 재배단지 859억)
      C) 관계사간 자산이동 / 투자·임대목적 — 신규 시공 의도 없음
         (경동나비엔 1,196억 "생산기지 통합에 따른 관계사간 자산 포트폴리오 조정")

    NEW_BUILD_INTENT 키워드(신축/건립/증축 등) 동반 시 살림 — 안전망.
    """
    title_compact = (title or "").replace(" ", "").replace("ㆍ", "").replace("·", "")
    if not any(p in title_compact for p in _ACQUISITION_REPORT_PATTERNS):
        return False
    if not body:
        return False
    target_matches = _ACQUISITION_TARGET_RE.findall(body)
    purpose_matches = _ACQUISITION_PURPOSE_RE.findall(body)
    target_text = " ".join(target_matches)
    purpose_text = " ".join(purpose_matches)
    combined = target_text + " " + purpose_text
    # 신축 의도가 있으면 무조건 살림 (예: "기존 농장 매입 후 신공장 건립" 같은 엣지)
    if any(kw in combined for kw in NEW_BUILD_INTENT_KEYWORDS):
        return False
    # A) commodity (취득자산이 백금 등 원자재)
    if any(kw in target_text for kw in COMMODITY_TARGET_KEYWORDS):
        return True
    # B) 농림업 (취득자산이 재배단지·농장 등)
    if any(kw in target_text for kw in AGRICULTURE_TARGET_KEYWORDS):
        return True
    # C) 관계사간 자산이동 / 임대수익 목적 (취득목적에 명시)
    if any(kw in purpose_text for kw in INTER_AFFILIATE_PURPOSE_KEYWORDS):
        return True
    return False


def _to_article(item: dict) -> Article | None:
    """DART 응답 1건을 Article 로 변환."""
    rcept_no = item.get("rcept_no") or ""
    report_nm = (item.get("report_nm") or "").strip()
    corp_name = (item.get("corp_name") or "").strip()
    if not rcept_no or not report_nm or not corp_name:
        return None

    # 게시일 (rcept_dt: "20260519")
    published = None
    raw_dt = item.get("rcept_dt") or ""
    if raw_dt and len(raw_dt) >= 8:
        try:
            dt_naive = datetime.strptime(raw_dt[:8], "%Y%m%d")
            published = dt_naive.replace(tzinfo=KST)
        except ValueError:
            pass

    url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

    # 보고서 유형별 영업 가치 라벨 — 1순위 신규시설투자 (골든타임) > 2순위 유형자산/공장 > 3순위 공급계약
    prio, kind_label = classify_report_kind(report_nm)
    prio_mark = {1: "⭐⭐⭐ 1순위", 2: "⭐⭐ 2순위", 3: "⭐ 3순위"}.get(prio, "기타")

    parts = [
        f"공시기업: {corp_name}",
        f"보고서명: {report_nm}",
        f"영업가치: {prio_mark}({kind_label})",
    ]
    if item.get("flr_nm") and item["flr_nm"] != corp_name:
        parts.append(f"제출인: {item['flr_nm']}")
    if item.get("corp_cls"):
        cls_map = {"Y": "유가증권", "K": "코스닥", "N": "코넥스", "E": "기타"}
        parts.append(f"시장: {cls_map.get(item['corp_cls'], item['corp_cls'])}")
    if item.get("rm"):
        parts.append(f"비고: {item['rm']}")
    if raw_dt:
        parts.append(f"접수일자: {raw_dt}")

    content = " | ".join(parts)
    # title 은 회사명 + 보고서명 합쳐서 — 룰 필터가 회사명/시설 키워드 매칭하기 쉽게
    title = f"[{corp_name}] {report_nm}"

    return Article(
        id=_make_id(rcept_no),
        source="dart.fss.or.kr",
        url=url,
        title=title,
        content=content,
        published_at=published,
    )


def fetch_page(api_key: str, bgn_de: str, end_de: str,
               page_no: int, page_count: int, verify_ssl: bool) -> tuple[list[dict], int]:
    """1페이지 조회. (items, total_page) 반환."""
    params = {
        "crtfc_key": api_key,
        "bgn_de": bgn_de,
        "end_de": end_de,
        "page_no": page_no,
        "page_count": page_count,
        "pblntf_ty": "I",   # 수시공시 (시설투자/유형자산 양수는 모두 I로 분류)
    }
    try:
        r = requests.get(LIST_ENDPOINT, params=params, timeout=REQUEST_TIMEOUT,
                         verify=verify_ssl,
                         headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        logger.warning("DART 요청 실패 (page=%d): %s", page_no, e)
        return [], 0
    except ValueError:
        logger.warning("DART 응답 JSON 파싱 실패")
        return [], 0

    status = data.get("status")
    if status != "000":
        # 013 = 조회된 데이터가 없습니다 (정상, 결과만 비어있음)
        if status == "013":
            return [], 0
        logger.warning("DART API 응답 상태 %s: %s", status, data.get("message"))
        return [], 0

    items = data.get("list") or []
    total_page = int(data.get("total_page") or 0)
    return items, total_page


# DART OpenAPI 는 corp_code 미지정 시 검색기간 한도 89일 (공식적으로 "3개월").
# 그 이상은 자동으로 청크 분할.
DART_CHUNK_DAYS = 89


def _collect_window(api_key: str, bgn_de: str, end_de: str, verify_ssl: bool,
                    apply_filter: bool, seen_ids: set, articles: list) -> None:
    """단일 [bgn_de, end_de] 윈도우 내 모든 페이지 호출. articles 에 in-place 추가."""
    logger.info("  ▸ 윈도우 %s ~ %s", bgn_de, end_de)
    page_count = 100
    page_no = 1
    total_page = None
    while True:
        t0 = time.time()
        items, tp = fetch_page(api_key, bgn_de, end_de, page_no, page_count, verify_ssl)
        if total_page is None:
            total_page = tp
            logger.info("     전체 페이지 %d (페이지당 %d)", total_page, page_count)
        if not items:
            break
        kept = 0
        filtered = 0
        for item in items:
            article = _to_article(item)
            if article is None:
                continue
            if article.id in seen_ids:
                continue
            if apply_filter:
                if not _matches_target(item.get("report_nm", "")):
                    filtered += 1
                    continue
            seen_ids.add(article.id)
            articles.append(article)
            kept += 1
        dt = time.time() - t0
        logger.info("     page %d: 응답 %d / 통과 %d / 제외 %d (%.2fs)",
                    page_no, len(items), kept, filtered, dt)
        if not total_page or page_no >= total_page:
            break
        page_no += 1
        time.sleep(0.3)


def collect(days: int, verify_ssl: bool, apply_filter: bool,
            fetch_bodies: bool = True) -> list[Article]:
    """최근 days 일 수시공시 수집 + 시설투자 관련만 필터.

    days > 89 이면 DART API 한도 회피를 위해 89일 청크로 나눠 호출 후 머지.

    fetch_bodies=True 면 각 공시 본문도 fetch 해서 article.content 에 prefix.
    카테고리 분류 정확도가 크게 향상되지만 첫 실행은 N×0.3초 지연 (캐시되면 즉시).
    """
    load_dotenv()
    api_key = os.environ.get("OPENDART_API_KEY", "").strip()
    if not api_key:
        logger.error("OPENDART_API_KEY 가 .env 에 설정되지 않았습니다.")
        logger.error("발급 가이드: docs/API_SETUP.md")
        return []

    today = datetime.now(KST)
    since = today - timedelta(days=days)

    # 청크 분할 — DART 는 89일 윈도우 공식 한도
    if days <= DART_CHUNK_DAYS:
        windows = [(since, today)]
    else:
        windows = []
        cur_until = today
        while cur_until > since:
            cur_since = max(cur_until - timedelta(days=DART_CHUNK_DAYS), since)
            windows.append((cur_since, cur_until))
            cur_until = cur_since
        logger.info("DART — 검색기간 %d일 → %d개 청크 (각 ≤%d일)",
                    days, len(windows), DART_CHUNK_DAYS)

    bgn_de = since.strftime("%Y%m%d")
    end_de = today.strftime("%Y%m%d")
    logger.info("DART 수시공시 수집: %s ~ %s (verify_ssl=%s, filter=%s)",
                bgn_de, end_de, verify_ssl, apply_filter)

    articles: list[Article] = []
    seen_ids: set[str] = set()
    for w_since, w_until in windows:
        _collect_window(
            api_key,
            w_since.strftime("%Y%m%d"),
            w_until.strftime("%Y%m%d"),
            verify_ssl, apply_filter, seen_ids, articles,
        )

    # === 본문 fetch (카테고리 분류 정확도 향상) ===
    if fetch_bodies and articles:
        logger.info("─" * 60)
        logger.info("본문 fetch 시작: %d건 (캐시 사용)", len(articles))
        from pathlib import Path as _Path
        rcept_re = __import__("re").compile(r"rcpNo=(\d+)")
        cache_dir = _Path("data/cache/dart_detail")
        body_t0 = time.time()
        cache_hit = 0
        fetched = 0
        failed = 0
        log_every = max(1, len(articles) // 10)  # 10% 마다 진행 로그
        for i, art in enumerate(articles, 1):
            m = rcept_re.search(art.url or "")
            if not m:
                continue
            rcept_no = m.group(1)
            cache_path = cache_dir / f"{rcept_no}.txt"
            was_cached = cache_path.exists()
            body = fetch_body(rcept_no, api_key, verify_ssl=verify_ssl, cache_dir=cache_dir)
            if body:
                # 본문을 content 앞에 prepend → categorize 가 본문도 함께 매칭
                art.content = f"{body} | {art.content}"
                if was_cached:
                    cache_hit += 1
                else:
                    fetched += 1
            else:
                failed += 1
            if i % log_every == 0 or i == len(articles):
                logger.info("  진행 %d/%d (캐시 %d / 신규 %d / 실패 %d, %.1fs)",
                            i, len(articles), cache_hit, fetched, failed,
                            time.time() - body_t0)
        logger.info("본문 fetch 완료: 캐시 %d / 신규 %d / 실패 %d (%.1fs)",
                    cache_hit, fetched, failed, time.time() - body_t0)

        # === 본문 기반 후처리 필터: 장비 단독 취득 공시 제외 ===
        # 보고서명만 보면 "유형자산 취득" 안에 토지매입(★)과 장비도입(노이즈)이 섞임.
        # 본문의 "취득목적물" 필드를 보고 후자만 골라서 제외.
        if apply_filter:
            before = len(articles)
            excluded_eq: list[str] = []
            kept_articles: list[Article] = []
            for a in articles:
                if _is_equipment_only_acquisition(a.title, a.content):
                    if len(excluded_eq) < 5:
                        excluded_eq.append(a.title)
                else:
                    kept_articles.append(a)
            articles = kept_articles
            removed = before - len(articles)
            if removed > 0:
                logger.info("장비단독취득 (시공 무관) 추가 제외: %d건 → 잔여 %d건",
                            removed, len(articles))
                for s in excluded_eq:
                    logger.info("  · %s", s)

            # === 본문 기반 후처리 필터: 이미 지어진 건물 매입 공시 제외 ===
            # 건설사 관점에서 시공 기회 0인 케이스 (공장·사옥 통째 매입, REITs 임대수익 등).
            # 단, 신축 의도(신축·건립·증축 등)가 함께 있으면 살림.
            before2 = len(articles)
            excluded_bldg: list[str] = []
            kept_articles2: list[Article] = []
            for a in articles:
                if _is_existing_building_acquisition(a.title, a.content):
                    if len(excluded_bldg) < 5:
                        excluded_bldg.append(a.title)
                else:
                    kept_articles2.append(a)
            articles = kept_articles2
            removed2 = before2 - len(articles)
            if removed2 > 0:
                logger.info("기존건물매입 (시공 무관) 추가 제외: %d건 → 잔여 %d건",
                            removed2, len(articles))
                for s in excluded_bldg:
                    logger.info("  · %s", s)

            # === 본문 기반 후처리 필터: 비건설 자산 (commodity / 농림업 / 관계사 자산이동) ===
            # 백금 등 원자재, 차 재배단지, 관계사간 자산 포트폴리오 조정 — 신규 시공 0.
            before3 = len(articles)
            excluded_noncon: list[str] = []
            kept_articles3: list[Article] = []
            for a in articles:
                if _is_noncon_acquisition(a.title, a.content):
                    if len(excluded_noncon) < 5:
                        excluded_noncon.append(a.title)
                else:
                    kept_articles3.append(a)
            articles = kept_articles3
            removed3 = before3 - len(articles)
            if removed3 > 0:
                logger.info("비건설 자산취득 (commodity/농림업/관계사이동) 추가 제외: %d건 → 잔여 %d건",
                            removed3, len(articles))
                for s in excluded_noncon:
                    logger.info("  · %s", s)

    logger.info("DART 수집 완료: %d건", len(articles))
    return articles


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14,
                        help="최근 N일치 (기본 14 — DART 시설공시는 빈도 낮음)")
    parser.add_argument("--output", help="출력 JSONL 경로 (생략 시 data/raw/dart_{date}.jsonl)")
    parser.add_argument("--insecure", action="store_true", help="SSL 검증 비활성화")
    parser.add_argument("--no-filter", action="store_true",
                        help="시설투자 관련 키워드 매칭 안 함 (전체 수시공시)")
    parser.add_argument("--no-body", action="store_true",
                        help="공시 본문 fetch 스킵 (디버깅용 — 카테고리 정확도 낮아짐)")
    args = parser.parse_args()

    if args.insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    articles = collect(days=args.days,
                       verify_ssl=not args.insecure,
                       apply_filter=not args.no_filter,
                       fetch_bodies=not args.no_body)

    if args.output:
        output_path = Path(args.output)
    else:
        today = datetime.now(KST).strftime("%Y-%m-%d")
        output_path = Path(f"data/raw/dart_{today}.jsonl")

    written = write_jsonl(output_path, articles)
    logger.info("─" * 60)
    logger.info("Stage 0 (DART) 완료: %d건 → %s", written, output_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
