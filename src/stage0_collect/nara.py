"""Stage 0 (보조): 나라장터(G2B) 입찰공고 수집기.

자이씨앤에이가 가장 직접적으로 영업할 수 있는 채널 — 입찰공고.
시설공사 카테고리만 가져와서, 첨단 산업시설(공장/연구소/플랜트/클린룸/물류센터 등)
키워드 매칭되는 공고만 저장.

인증키 발급:
    https://www.data.go.kr/data/15129394/openapi.do → 활용신청 → 즉시 발급
    .env 의 G2B_API_KEY 에 추가

사용법:
    python -m src.stage0_collect.nara --days 7
    python -m src.stage0_collect.nara --days 7 --insecure
    python -m src.stage0_collect.nara --days 7 --no-filter  # 시설 키워드 매칭 안 함 (전체)
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import requests
import urllib3
from dotenv import load_dotenv

from ..common.io import write_jsonl
from ..common.schema import Article

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))

# 나라장터 입찰공고정보서비스 — 시설공사(Cnstwk) 조회
BASE_URL = "http://apis.data.go.kr/1230000/ad/BidPublicInfoService"
ENDPOINT_CNSTWK = f"{BASE_URL}/getBidPblancListInfoCnstwk"

# ─── 두 단계 필터 ───
# (1) STRONG_KEYWORDS: 단독으로 매칭되면 무조건 통과 (의심 여지 없는 첨단산업)
STRONG_KEYWORDS = [
    # 반도체·디스플레이
    "반도체", "디스플레이", "OLED", "FAB", "클린룸", "패키징", "OSAT",
    # 제약·바이오
    "GMP", "CGMP", "CDMO", "바이오플랜트", "백신공장", "원료의약품", "완제의약품",
    "제약공장", "바이오공장", "의약품공장",
    # 이차전지
    "기가팩토리", "배터리셀", "양극재", "음극재", "분리막",
    # R&D
    "R&D센터", "연구개발센터", "혁신센터", "기술원",
    # 기타 첨단
    "데이터센터", "물류센터", "물류창고",
]

# (2) 부드러운 매칭: SHELL(시설 일반) + INDUSTRY(산업 신호) 둘 다 매칭이어야 통과
#    예: "단지" (shell) + "반도체" (industry) → 통과
#        "단지" (shell) 만 있고 산업 신호 없으면 → 탈락
SHELL_KEYWORDS = [
    "공장", "플랜트", "라인", "캠퍼스", "단지", "센터", "시설",
    "신축공사", "증설", "신설", "기지",
]
INDUSTRY_SIGNALS = [
    # 첨단 산업 도메인
    "반도체", "디스플레이", "OLED", "배터리", "이차전지", "제약", "바이오",
    "백신", "원료의약품", "의약품", "화장품", "식품", "음료",
    "연구", "R&D", "실증", "혁신", "기술",
    # 기업명 신호 (대기업 발주는 자이씨앤에이 타겟)
    "삼성", "SK하이닉스", "LG", "현대차", "기아", "포스코", "한화", "롯데",
    "셀트리온", "녹십자", "유한양행", "한미", "종근당", "대웅", "보령",
    # 기타 산업 키워드
    "스마트팩토리", "산업", "제조",
]

# 제외 키워드 — 공고명에 매칭되면 무조건 탈락 (강한 노이즈)
EXCLUDE_KEYWORDS = [
    # 주택·숙소
    "아파트", "오피스텔", "공동주택", "임대주택", "주거",
    "단독주택", "다세대", "재건축", "재개발", "주상복합",
    "사택", "기숙사", "관사", "원룸", "빌라", "타운하우스",
    # 관급 인프라
    "도로", "교량", "터널", "고속도로", "철도", "지하철", "전철",
    "항만", "방파제", "송전", "변전", "송배전", "전력망",
    "상수도", "하수도", "정수장", "하수처리", "폐수처리", "공공폐수",
    "공업용수", "가스배관", "댐", "저수지", "통신망", "광케이블",
    # 공공시설
    "시청", "구청", "군청", "도청", "관공서", "공무원",
    "공원", "체육관", "야구장", "축구장", "경기장", "수영장",
    "도서관", "박물관", "미술관", "문화회관", "문화센터",
    "마을회관", "주민센터", "경로당", "노인회관",
    "초등학교", "중학교", "고등학교", "유치원", "어린이집",
    "특수학교", "복지관", "재활시설", "직업재활",
    # 의료
    "종합병원", "대학병원", "요양원", "요양병원", "보건소", "보건지소",
    # 농업·조경 (자이씨앤에이 영역 아님)
    "농공단지", "농어촌", "스마트원예", "체험센터", "체험관",
    "수목", "전정", "예초", "제초", "조경", "녹지", "조림",
    "농업기술원", "농업기술센터",
    "농업복합", "바이오농업", "스마트팜", "축산", "양식장",
    "가로화단", "화단", "수목원",
    # 산업단지 부속 정비/조경 (단지 자체가 아닌 그 안의 잡일)
    "환경정비", "인도정비", "보도\\s*개선", "보도개선", "도로\\s*개선",
    "유지관리", "풀베기", "잡초", "노면\\s*포장",
    "빗물받이", "준설", "오수관", "우수관", "맨홀",
    # 골프장·관광
    "골프장", "골프", "라운지", "리조트", "관광단지", "휴양",
    "펜션", "캠핑장", "글램핑",
    # 보수·교체 (소규모 유지보수)
    "교체공사", "보수공사", "보강공사", "노후",
    "철거", "해체", "리모델링",
    "냉난방기", "환기설비\\s*교체", "조리실", "식당",
    "마감재", "도배", "벽지",
    "승강기", "엘리베이터", "에스컬레이터",
    "누수공사", "누수\\s*공사", "방수공사", "도장공사",
    # 토목
    "옹벽", "보강토", "사면", "법면",
    "진출입로", "도로\\s*포장", "토목공사",
    # 학교 캠퍼스 (대학 부속 시설)
    "글로벌캠퍼스", "그린캠퍼스", "교육원", "연수원",
    "대학교\\s*캠퍼스", "캠퍼스\\s*노후", "캠퍼스\\s*전기",
    "교직원", "기숙사", "신어관", "대학혁신지원사업",
]

REQUEST_TIMEOUT = 20
USER_AGENT = "sujoo-radar/0.1 (xicna nara client)"


def _make_id(bid_no: str) -> str:
    """입찰공고번호 md5 → 멱등 ID."""
    return hashlib.md5(f"g2b:{bid_no}".encode("utf-8")).hexdigest()[:16]


def _matches_target(name: str) -> bool:
    """공고명 매칭 — 두 단계.
    (1) STRONG_KEYWORDS 단독 매칭이면 통과
    (2) SHELL + INDUSTRY 동시 매칭이면 통과
    """
    if any(kw in name for kw in STRONG_KEYWORDS):
        return True
    has_shell = any(kw in name for kw in SHELL_KEYWORDS)
    has_industry = any(kw in name for kw in INDUSTRY_SIGNALS)
    return has_shell and has_industry


def _matches_exclude(name: str) -> bool:
    """공고명에 제외 키워드가 있는가? (정규식 패턴도 처리)"""
    import re as _re
    for kw in EXCLUDE_KEYWORDS:
        if "\\s" in kw:
            if _re.search(kw, name):
                return True
        elif kw in name:
            return True
    return False


def _fmt_dt(dt: datetime) -> str:
    """나라장터 포맷 YYYYMMDDhhmm."""
    return dt.strftime("%Y%m%d%H%M")


def _to_article(item: dict) -> Article | None:
    """나라장터 응답 1건을 Article 로 변환. 실패 시 None."""
    bid_no = item.get("bidNtceNo") or item.get("bidNtceOrd") or ""
    name = (item.get("bidNtceNm") or "").strip()
    if not bid_no or not name:
        return None

    # 게시일 (bidNtceDt: "2026-05-19 14:30:00" 같은 형식)
    published = None
    raw_dt = item.get("bidNtceDt") or item.get("bidNtceRgstDt") or ""
    if raw_dt:
        try:
            # 다양한 포맷 시도
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M", "%Y-%m-%d"):
                try:
                    dt_naive = datetime.strptime(raw_dt.strip(), fmt)
                    published = dt_naive.replace(tzinfo=KST)
                    break
                except ValueError:
                    continue
        except Exception:
            pass

    # content 합성: 발주기관·예가·사업장소·업무구분 등을 한 문자열로
    parts = []
    if item.get("ntceInsttNm"):
        parts.append(f"공고기관: {item['ntceInsttNm']}")
    if item.get("dminsttNm"):
        parts.append(f"수요기관: {item['dminsttNm']}")
    if item.get("presmptPrce"):
        try:
            won = int(float(item["presmptPrce"]))
            parts.append(f"추정가격: {won:,}원")
        except (ValueError, TypeError):
            parts.append(f"추정가격: {item['presmptPrce']}")
    if item.get("presnatnOprtnPlce"):
        parts.append(f"사업장소: {item['presnatnOprtnPlce']}")
    if item.get("bsnsDivNm"):
        parts.append(f"업무구분: {item['bsnsDivNm']}")
    if item.get("opengDt"):
        parts.append(f"개찰일시: {item['opengDt']}")
    if item.get("bidNtceDtlUrl"):
        url = item["bidNtceDtlUrl"]
    else:
        url = f"https://www.g2b.go.kr/pt/menu/selectSubFrame.do?framesrc=/pt/menu/frameTgong.do?url=https://www.g2b.go.kr:8101/ep/invitation/publish/bidInfoDtl.do?bidno={bid_no}"

    content = " | ".join(parts)

    return Article(
        id=_make_id(bid_no),
        source="g2b.go.kr",
        url=url,
        title=name,
        content=content,
        published_at=published,
    )


def fetch_page(api_key: str, since: datetime, until: datetime,
               page: int, rows: int, verify_ssl: bool) -> tuple[list[dict], int]:
    """1페이지 호출. (items, totalCount) 반환."""
    params = {
        "serviceKey": api_key,        # 인코딩된 키 그대로 (requests 가 추가 인코딩 안 함)
        "pageNo": page,
        "numOfRows": rows,
        "inqryDiv": 1,                 # 1: 공고게시일 기준
        "inqryBgnDt": _fmt_dt(since),
        "inqryEndDt": _fmt_dt(until),
        "type": "json",
    }
    # serviceKey 가 이미 URL 인코딩된 상태라면 requests 가 또 인코딩하면 깨짐 → preserve
    # 따라서 직접 query string 구성
    from urllib.parse import urlencode
    qs = urlencode({k: v for k, v in params.items() if k != "serviceKey"})
    full_url = f"{ENDPOINT_CNSTWK}?serviceKey={api_key}&{qs}"

    try:
        r = requests.get(full_url, timeout=REQUEST_TIMEOUT, verify=verify_ssl,
                         headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        logger.warning("G2B 요청 실패 (page=%d): %s", page, e)
        return [], 0
    except ValueError:
        logger.warning("G2B 응답 JSON 파싱 실패 (page=%d). 응답 일부: %s",
                       page, (r.text or "")[:300])
        return [], 0

    # 응답 구조: {"response": {"header": {...}, "body": {"items": [...], "totalCount": N, ...}}}
    body = (data.get("response") or {}).get("body") or {}
    header = (data.get("response") or {}).get("header") or {}

    result_code = header.get("resultCode") or header.get("RESULT_CODE")
    if result_code and str(result_code) != "00":
        logger.warning("G2B API 오류 응답: code=%s msg=%s",
                       result_code, header.get("resultMsg") or header.get("RESULT_MSG"))
        return [], 0

    items = body.get("items") or []
    if isinstance(items, dict):  # 일부 응답은 {"item": [...]} 구조
        items = items.get("item") or []
    if isinstance(items, dict):  # 1건이면 dict로 옴
        items = [items]

    total = int(body.get("totalCount") or 0)
    return items, total


# G2B API 는 검색 윈도우가 너무 넓으면 totalCount=0 으로 응답하는 묵시적 한도가 있음
# (정확한 한도는 미공개. 실측상 30일 윈도우는 안정적이라 청크 단위로 사용).
G2B_CHUNK_DAYS = 30


def _collect_window(api_key: str, since, until, verify_ssl: bool,
                    apply_filter: bool, seen_ids: set,
                    articles: list, max_pages: int | None = None) -> None:
    """단일 [since, until] 윈도우 내 페이지 호출 (max_pages 제한 가능). in-place 추가."""
    logger.info("  ▸ 윈도우 %s ~ %s%s",
                since.strftime("%Y-%m-%d"), until.strftime("%Y-%m-%d"),
                f" (max {max_pages} page)" if max_pages else "")
    rows_per_page = 100
    page = 1
    total = None
    while True:
        t0 = time.time()
        items, total_count = fetch_page(api_key, since, until, page, rows_per_page, verify_ssl)
        if total is None:
            total = total_count
            logger.info("     전체 시설공사 공고 %d건 (페이지당 %d)", total, rows_per_page)
        if not items:
            break
        kept = 0
        filtered_target = 0
        filtered_exclude = 0
        for item in items:
            article = _to_article(item)
            if article is None:
                continue
            if article.id in seen_ids:
                continue
            if apply_filter:
                if _matches_exclude(article.title):
                    filtered_exclude += 1
                    continue
                if not _matches_target(article.title):
                    filtered_target += 1
                    continue
            seen_ids.add(article.id)
            articles.append(article)
            kept += 1
        dt = time.time() - t0
        logger.info("     page %d: 응답 %d / 통과 %d / 제외(타겟불일치 %d, 제외키워드 %d) (%.2fs)",
                    page, len(items), kept, filtered_target, filtered_exclude, dt)
        if total == 0 or page * rows_per_page >= total:
            break
        if max_pages is not None and page >= max_pages:
            logger.info("     max_pages=%d 도달 — 윈도우 종료 (전체 %d페이지 중 일부)",
                        max_pages, (total + rows_per_page - 1) // rows_per_page)
            break
        page += 1
        time.sleep(0.3)  # rate limit 예의


def collect(days: int, verify_ssl: bool, apply_filter: bool,
            max_pages_per_window: int | None = None) -> list[Article]:
    """최근 days 일 시설공사 입찰공고 수집.

    days > 30 이면 G2B API 한도 회피를 위해 30일 청크로 나눠 호출 후 머지.
    max_pages_per_window 가 지정되면 각 청크 내에서 첫 N페이지만 sweep
    (G2B API 가 매우 느려 풀 sweep 이 비현실적인 경우 사용).
    """
    load_dotenv()
    api_key = os.environ.get("G2B_API_KEY", "").strip()
    if not api_key:
        logger.error("G2B_API_KEY 가 .env 에 설정되지 않았습니다.")
        logger.error("발급 가이드: docs/API_SETUP.md")
        return []

    until = datetime.now(KST)
    since = until - timedelta(days=days)

    # 청크 분할 — G2B 는 30일 윈도우 한도 추정
    if days <= G2B_CHUNK_DAYS:
        windows = [(since, until)]
    else:
        windows = []
        cur_until = until
        while cur_until > since:
            cur_since = max(cur_until - timedelta(days=G2B_CHUNK_DAYS), since)
            windows.append((cur_since, cur_until))
            cur_until = cur_since
        logger.info("나라장터 — 검색기간 %d일 → %d개 청크 (각 ≤%d일)",
                    days, len(windows), G2B_CHUNK_DAYS)

    logger.info("나라장터 시설공사 입찰공고 수집: %s ~ %s (verify_ssl=%s, filter=%s)",
                since.strftime("%Y-%m-%d %H:%M"),
                until.strftime("%Y-%m-%d %H:%M"),
                verify_ssl, apply_filter)

    articles: list[Article] = []
    seen_ids: set[str] = set()
    for w_since, w_until in windows:
        _collect_window(api_key, w_since, w_until, verify_ssl,
                        apply_filter, seen_ids, articles,
                        max_pages=max_pages_per_window)

    logger.info("나라장터 수집 완료: 총 %d건", len(articles))
    return articles


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7, help="최근 N일치 (기본 7)")
    parser.add_argument("--output", help="출력 JSONL 경로 (생략 시 data/raw/g2b_{date}.jsonl)")
    parser.add_argument("--max-pages", type=int, default=None,
                        help="각 청크 윈도우 내 sweep 페이지 최대 한도. "
                             "지정하지 않으면 무제한(전체 sweep). G2B API 가 느린 환경에서 "
                             "타임아웃 회피용. 100건/페이지.")
    parser.add_argument("--insecure", action="store_true",
                        help="SSL 검증 비활성화")
    parser.add_argument("--no-filter", action="store_true",
                        help="자이씨앤에이 영역 키워드 매칭 안 함 (전체 시설공사)")
    args = parser.parse_args()

    if args.insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    articles = collect(days=args.days,
                       verify_ssl=not args.insecure,
                       apply_filter=not args.no_filter,
                       max_pages_per_window=args.max_pages)

    if args.output:
        output_path = Path(args.output)
    else:
        today = datetime.now(KST).strftime("%Y-%m-%d")
        output_path = Path(f"data/raw/g2b_{today}.jsonl")

    written = write_jsonl(output_path, articles)
    logger.info("─" * 60)
    logger.info("Stage 0 (G2B) 완료: %d건 → %s", written, output_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
