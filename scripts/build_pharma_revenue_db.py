"""DART OpenAPI 로 제약·바이오사 매출 DB 자동 구축.

영업 활용:
  500억+ 공장 발주 여력 있는 회사 = 매출 약 3,000억+ (또는 CDMO/바이오 신약사).
  이 스크립트가 한 번 돌면 data/cache/pharma_revenue.json 에 회사명 → 매출 매핑 생성.
  daily_report_html.py 의 MFDS 카드 필터가 이 파일을 참조.

실행:
  python scripts/build_pharma_revenue_db.py
  python scripts/build_pharma_revenue_db.py --year 2024
  python scripts/build_pharma_revenue_db.py --year 2024 --insecure
  python scripts/build_pharma_revenue_db.py --force  # 캐시 무시하고 재빌드

주기: 매년 1회 (3~4월 사업보고서 발표 후) 실행이면 충분.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path

import requests
import urllib3
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DART_CORP_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
DART_FIN_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"

CACHE_DIR = _ROOT / "data" / "cache"
CORP_CACHE = CACHE_DIR / "dart_corp_codes.json"   # 회사명+corp_code 캐시
REV_CACHE = CACHE_DIR / "pharma_revenue.json"     # 제약사 매출 DB

# 제약·바이오 회사명 매칭 키워드 (corp_name 에 포함 시 후보)
# 정밀 매칭: "동국" 같은 모호한 키워드는 "동국제약" 처럼 명확한 형태로만.
PHARMA_NAME_KEYWORDS = [
    "제약", "약품", "바이오", "팜텍", "메디",
    "헬스케어", "라이프사이언스",
    "녹십자", "셀트리온", "유한양행", "광동제약",
    "보령", "한미약품", "한미사이언스", "종근당", "대웅", "JW중외", "JW생명",
    "씨젠", "휴젤", "메디톡스", "휴온스", "휴메딕스",
    "삼성바이오", "SK바이오", "롯데바이오",
    "콜마", "코스맥스", "에이프로젠", "프레스티지바이오",
    "지놈앤", "올릭스", "리가켐", "알테오젠", "에스티팜",
    "차바이오", "메디포스트", "큐로셀", "압타바이오",
    "보로노이", "엔지켐", "이노엔",
    "동국제약", "동화약품", "동아쏘시오", "동아에스티", "동아제약",
    "일동", "환인제약", "삼진제약", "안국약품", "신풍제약",
    "부광약품", "영진약품", "경동제약", "제일약품", "이연제약",
    "유유제약", "국제약품", "현대약품", "대화제약", "삼일제약",
    "삼천당", "비씨월드", "신신제약", "대원제약", "명문제약",
    "동성제약", "한독", "팜젠사이언스", "알리코제약", "화일약품",
    "셀바이오텍", "위더스제약", "JW신약", "대한약품", "대한뉴팜",
    "메디카코리아", "한국유나이티드제약", "한올바이오",
    "콜마비앤에이치", "에이비엘", "오스코텍", "지아이이노베이션",
]

# 명백히 제약·바이오 아닌 회사 (이름에 키워드 들어가지만 다른 업종)
EXCLUDE_NAMES = [
    # 농업/사료
    "팜스코", "팜스토리", "농협사료", "이팜",
    # 부동산/IT/통신/철강
    "비전팜", "케이티스카이라이프", "이지바이오",
    "동국제강", "동국씨엠", "동국홀딩스", "동국산업",
    "DL바이오", "한국바이오",  # 동명이인 회사 노이즈
]

# 500억+ 공장 발주 가능한 매출 기준 (원). CAPEX/매출 ≥ 15% 가정 시
# 단일 500억 공장 부담 가능 = 매출 3,000억+
REVENUE_THRESHOLD = 300_000_000_000   # 3,000억 원

# CDMO/바이오 신약사 — 매출 작아도 시설투자 비중 큼 → 무조건 통과
CDMO_BIO_BYPASS = [
    "삼성바이오로직스", "SK바이오사이언스", "SK바이오팜",
    "셀트리온", "셀트리온헬스케어", "셀트리온제약",
    "에스티팜", "프레스티지바이오로직스", "롯데바이오로직스",
    "바이넥스", "에이프로젠바이오로직스", "차바이오텍",
    "휴젤", "메디톡스", "메디포스트",
    "알테오젠", "리가켐바이오", "지놈앤컴퍼니",
    "큐로셀", "보로노이", "코스맥스파마",
]


def fetch_corp_codes(api_key: str, verify_ssl: bool) -> list[dict]:
    """DART 전체 회사 corp_code 명단 다운로드 + XML 파싱."""
    log.info("DART corp_code.xml 다운로드 중...")
    r = requests.get(DART_CORP_URL, params={"crtfc_key": api_key},
                     timeout=60, verify=verify_ssl)
    r.raise_for_status()
    log.info("응답 %d B (zip)", len(r.content))

    # ZIP 안에 CORPCODE.xml
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        with z.open(z.namelist()[0]) as f:
            xml_data = f.read()
    log.info("XML %d B", len(xml_data))

    root = ET.fromstring(xml_data)
    corps = []
    for li in root.findall("list"):
        corp_code = (li.findtext("corp_code") or "").strip()
        corp_name = (li.findtext("corp_name") or "").strip()
        stock_code = (li.findtext("stock_code") or "").strip()
        if corp_code and corp_name:
            corps.append({
                "corp_code": corp_code,
                "corp_name": corp_name,
                "stock_code": stock_code,
            })
    log.info("전체 회사 %d개 파싱", len(corps))
    return corps


def filter_pharma(corps: list[dict], listed_only: bool = True) -> list[dict]:
    """제약·바이오 후보 추출. listed_only=True 면 상장사(stock_code 있음)만."""
    candidates = []
    for c in corps:
        name = c["corp_name"]
        # 명시적 제외 (제약 아닌 회사)
        if any(ex in name for ex in EXCLUDE_NAMES):
            continue
        # 상장사만 (비상장은 DART 사업보고서 안 올려서 매출 fetch 불가능 多)
        if listed_only and not c.get("stock_code"):
            continue
        if any(kw in name for kw in PHARMA_NAME_KEYWORDS):
            candidates.append(c)
    candidates.sort(key=lambda x: x["corp_name"])
    log.info("제약·바이오 상장 후보: %d개", len(candidates))
    return candidates


def fetch_revenue(api_key: str, corp_code: str, year: int,
                  verify_ssl: bool) -> int | None:
    """단일 회사 매출액 fetch (사업보고서). 실패 시 None.

    reprt_code:
      11011 = 사업보고서 (1년치) ← 매출 정보 가장 정확
      11013 = 1분기, 11012 = 반기, 11014 = 3분기
    fs_div: CFS=연결재무제표, OFS=별도재무제표
    """
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": "11011",
        "fs_div": "CFS",
    }
    try:
        r = requests.get(DART_FIN_URL, params=params, timeout=15, verify=verify_ssl)
        r.raise_for_status()
        d = r.json()
    except (requests.RequestException, ValueError):
        return None

    if d.get("status") not in ("000", "013"):
        return None
    items = d.get("list") or []
    if not items:
        # 연결재무제표 없으면 별도재무제표 재시도
        params["fs_div"] = "OFS"
        try:
            r = requests.get(DART_FIN_URL, params=params, timeout=15, verify=verify_ssl)
            r.raise_for_status()
            d = r.json()
        except (requests.RequestException, ValueError):
            return None
        if d.get("status") not in ("000", "013"):
            return None
        items = d.get("list") or []

    # 매출액 추출 — DART 응답에서 sj_div 는 회사마다 다름:
    #   IS  = 손익계산서 (단순 양식)
    #   CIS = 포괄손익계산서 (K-IFRS, 대부분 회사) ← 매출액 여기 들어감
    # account_id 가 ifrs-full_Revenue 면 100% 매출액. account_nm 도 fallback.
    for it in items:
        acc_id = it.get("account_id") or ""
        acc_nm = (it.get("account_nm") or "").strip()
        sj_div = it.get("sj_div") or ""
        if sj_div not in ("IS", "CIS"):
            continue
        if acc_id == "ifrs-full_Revenue" or \
           acc_nm in ("매출액", "수익(매출액)", "영업수익", "수익", "매출"):
            amt_str = (it.get("thstrm_amount") or "").replace(",", "").replace("(", "-").replace(")", "")
            try:
                return int(amt_str)
            except ValueError:
                pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=datetime.now().year - 1,
                    help="회계연도 (기본: 작년)")
    ap.add_argument("--insecure", action="store_true")
    ap.add_argument("--force", action="store_true", help="캐시 무시")
    ap.add_argument("--max-corps", type=int, default=300, help="fetch 시도 최대 회사 수")
    args = ap.parse_args()

    if args.insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    load_dotenv()
    api_key = os.environ.get("OPENDART_API_KEY", "").strip()
    if not api_key:
        log.error("OPENDART_API_KEY 가 .env 에 없음")
        return

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    verify = not args.insecure

    # ─── 1. corp_code 명단 (캐시 우선) ───
    if CORP_CACHE.exists() and not args.force:
        corps = json.loads(CORP_CACHE.read_text(encoding="utf-8"))
        log.info("corp_code 캐시 사용: %d개", len(corps))
    else:
        corps = fetch_corp_codes(api_key, verify)
        CORP_CACHE.write_text(json.dumps(corps, ensure_ascii=False), encoding="utf-8")
        log.info("corp_code 캐시 저장: %s", CORP_CACHE)

    # ─── 2. 제약·바이오 후보 추출 ───
    candidates = filter_pharma(corps)
    if len(candidates) > args.max_corps:
        candidates = candidates[: args.max_corps]
        log.info("max-corps=%d 로 잘림", args.max_corps)

    # ─── 3. 매출 fetch ───
    log.info("매출 fetch 시작 (%d개, year=%d)...", len(candidates), args.year)
    revenue_db: dict[str, dict] = {}
    succeed = 0
    failed = 0
    for i, c in enumerate(candidates, 1):
        rev = fetch_revenue(api_key, c["corp_code"], args.year, verify)
        if rev is not None and rev > 0:
            revenue_db[c["corp_name"]] = {
                "corp_code": c["corp_code"],
                "stock_code": c["stock_code"],
                "revenue": rev,
                "year": args.year,
            }
            succeed += 1
        else:
            failed += 1
        if i % 20 == 0:
            log.info("  진행 %d/%d (성공 %d, 실패 %d)", i, len(candidates), succeed, failed)
        time.sleep(0.1)  # DART API rate limit: 분당 1000 호출

    log.info("매출 fetch 완료: 성공 %d / 실패 %d", succeed, failed)

    # ─── 4. 임계값 통과 필터 + bypass 추가 ───
    qualified = {}
    for name, info in revenue_db.items():
        rev_b = info["revenue"] // 100_000_000
        if info["revenue"] >= REVENUE_THRESHOLD:
            qualified[name] = {**info, "reason": f"매출 {rev_b:,}억"}
        else:
            # CDMO/바이오 bypass 매칭 — 매출 작아도 시설투자 비중 큼
            for byp in CDMO_BIO_BYPASS:
                if byp in name:
                    qualified[name] = {**info,
                                       "reason": f"CDMO/바이오 bypass (매출 {rev_b:,}억)"}
                    break

    # bypass 목록 中 DART fetch 자체가 안 된 회사도 수동 등재 (revenue=0)
    for byp in CDMO_BIO_BYPASS:
        if not any(byp in q for q in qualified):
            qualified[byp] = {"corp_code": "", "stock_code": "", "revenue": 0,
                              "year": args.year,
                              "reason": "CDMO/바이오 bypass (DART fetch 실패)"}

    log.info("매출 3,000억+ or CDMO/바이오 통과: %d개사", len(qualified))

    # 정렬: 매출 큰 순
    sorted_keys = sorted(qualified, key=lambda k: qualified[k]["revenue"], reverse=True)
    out = {
        "generated_at": datetime.now().isoformat(),
        "year": args.year,
        "threshold_won": REVENUE_THRESHOLD,
        "count": len(qualified),
        "companies": {k: qualified[k] for k in sorted_keys},
    }
    REV_CACHE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("저장: %s", REV_CACHE)

    # 미리보기 Top 30
    print()
    print(f"=== 통과 회사 Top 30 / 전체 {len(qualified)}개 ===")
    for k in sorted_keys[:30]:
        v = qualified[k]
        rev_b = v["revenue"] // 100_000_000
        print(f"  {rev_b:>8,}억 | {k:30s} | {v['reason']}")


if __name__ == "__main__":
    main()
