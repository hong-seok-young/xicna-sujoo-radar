"""RSS 피드 100개 전수 검증.

crawling_sites_final.md 에 정리된 RSS URL 100개를 모두 두드려서
- HTTP 도달 가능한가?
- RSS/Atom 으로 파싱되는가?
- entry 가 실제로 1개라도 있는가?
- 최근(7일 이내) 기사가 있는가?

결과를 data/rss_validation_{YYYY-MM-DD}.md 로 저장.

사용법:
    python scripts/validate_rss.py
    python scripts/validate_rss.py --insecure   # 사내 SSL Inspection 환경
    python scripts/validate_rss.py --workers 20 # 동시 요청 수 (기본 10)
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests
from dateutil import parser as dateparser

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = 15

# ─────────────────────────────────────────────────────────────────────────
# 100개 RSS 후보 (crawling_sites_final.md 2부 기준)
# (category, name, domain, url)
# ─────────────────────────────────────────────────────────────────────────
CANDIDATES: list[tuple[str, str, str, str]] = [
    # 통신사
    ("통신사", "연합뉴스",   "yonhapnews.co.kr",   "https://www.yonhapnews.co.kr/rss/index.xml"),
    ("통신사", "연합뉴스TV", "yonhapnewstv.co.kr", "https://www.yonhapnewstv.co.kr/browse/feed/"),
    ("통신사", "뉴시스",     "newsis.com",         "https://www.newsis.com/RSS/allnews.xml"),
    ("통신사", "뉴스1",      "news1.kr",           "https://www.news1.kr/rss/allnews.xml"),
    # 종합 일간지
    ("종합일간지", "조선일보", "chosun.com",        "https://rssplus.chosun.com/"),
    ("종합일간지", "동아일보", "donga.com",         "http://rss.donga.com/total.xml"),
    ("종합일간지", "중앙일보", "joongang.co.kr",    "https://rss.joins.com/joins_news_list.xml"),
    ("종합일간지", "한겨레",   "hani.co.kr",        "https://www.hani.co.kr/rss/"),
    ("종합일간지", "경향신문", "khan.co.kr",        "https://www.khan.co.kr/rss/rssdata/total_news.xml"),
    ("종합일간지", "한국일보", "hankookilbo.com",   "https://www.hankookilbo.com/rss/"),
    ("종합일간지", "국민일보", "kmib.co.kr",        "https://www.kmib.co.kr/rss/data/kmibRssAll.xml"),
    ("종합일간지", "세계일보", "segye.com",         "https://www.segye.com/Article/RssService"),
    ("종합일간지", "문화일보", "munhwa.com",        "https://www.munhwa.com/rss/"),
    ("종합일간지", "서울신문", "seoul.co.kr",       "https://www.seoul.co.kr/rss/"),
    # 경제·비즈니스
    ("경제",   "매일경제",       "mk.co.kr",         "https://www.mk.co.kr/rss/30000001/"),
    ("경제",   "한국경제",       "hankyung.com",     "http://rss.hankyung.com/feed/economy.xml"),
    ("경제",   "서울경제",       "sedaily.com",      "https://rss.sedaily.com/allnews"),
    ("경제",   "헤럴드경제",     "heraldcorp.com",   "http://rss.heraldcorp.com/news.xml"),
    ("경제",   "파이낸셜뉴스",   "fnnews.com",       "https://www.fnnews.com/rss/r20/fn_realnews_economy.xml"),
    ("경제",   "이데일리",       "edaily.co.kr",     "https://rss.edaily.co.kr/edaily_allnews.xml"),
    ("경제",   "아시아경제",     "asiae.co.kr",      "https://www.asiae.co.kr/rss/allnews.xml"),
    ("경제",   "머니투데이",     "mt.co.kr",         "https://rss.mt.co.kr/mt_news1.xml"),
    ("경제",   "뉴스핌",         "newspim.com",      "https://www.newspim.com/rss/allnews.xml"),
    ("경제",   "이투데이",       "etoday.co.kr",     "https://www.etoday.co.kr/rss/"),
    ("경제",   "비즈니스워치",   "bizwatch.co.kr",   "https://news.bizwatch.co.kr/rss/"),
    ("경제",   "비즈조선",       "biz.chosun.com",   "https://biz.chosun.com/rss/"),
    # 방송사
    ("방송",   "KBS뉴스",  "news.kbs.co.kr",   "https://news.kbs.co.kr/rss/news.xml"),
    ("방송",   "MBC뉴스",  "imnews.imbc.com",  "https://imnews.imbc.com/rss/news/news_00.xml"),
    ("방송",   "SBS뉴스",  "news.sbs.co.kr",   "https://news.sbs.co.kr/news/rss.do"),
    ("방송",   "JTBC뉴스", "news.jtbc.co.kr",  "https://news.jtbc.co.kr/rss/news.xml"),
    ("방송",   "YTN",      "ytn.co.kr",        "https://www.ytn.co.kr/rss/"),
    ("방송",   "MBN",      "mbn.co.kr",        "https://mbnmoney.mbn.co.kr/rss/all"),
    ("방송",   "TV조선",   "tvchosun.com",     "https://news.tvchosun.com/site/data/rss/rss.xml"),
    ("방송",   "채널A",    "ichannela.com",    "https://www.ichannela.com/news/rss.xml"),
    # IT·디지털·과학
    ("IT", "전자신문",       "etnews.com",       "https://www.etnews.com/rss/allnews.xml"),
    ("IT", "디지털타임스",   "dt.co.kr",         "https://www.dt.co.kr/rss/"),
    ("IT", "ZDNet Korea",   "zdnet.co.kr",      "https://zdnet.co.kr/rss/"),
    ("IT", "아이뉴스24",     "inews24.com",      "https://www.inews24.com/rss/"),
    ("IT", "블로터",         "bloter.net",       "https://www.bloter.net/feed"),
    ("IT", "디일렉",         "thelec.kr",        "https://thelec.kr/feed"),
    ("IT", "IT조선",         "it.chosun.com",    "https://it.chosun.com/rss/"),
    ("IT", "KEIT",          "keit.re.kr",       "https://www.keit.re.kr/rss/"),
    ("IT", "테크M",          "techm.kr",         "https://www.techm.kr/rss/"),
    ("IT", "바이트타임즈",   "bytetimes.co.kr",  "https://www.bytetimes.co.kr/rss/"),
    ("IT", "넥스트데일리",   "nextdaily.co.kr",  "https://www.nextdaily.co.kr/rss/"),
    ("IT", "데이터넷",       "datanet.co.kr",    "https://www.datanet.co.kr/rss/"),
    # 산업·제조·반도체
    ("산업제조", "반도체신문",       "semicon.co.kr",      "https://www.semicon.co.kr/rss/"),
    ("산업제조", "아이씨엔 매거진",  "icnweb.co.kr",       "https://www.icnweb.co.kr/rss/"),
    ("산업제조", "EE Times Korea",   "eetkorea.com",       "https://www.eetkorea.com/rss/"),
    ("산업제조", "THE GURU",         "theguru.co.kr",      "https://www.theguru.co.kr/rss/"),
    ("산업제조", "기계설비신문",     "kmecnews.co.kr",     "https://www.kmecnews.co.kr/rss/"),
    ("산업제조", "키포스크",         "kipost.net",         "https://kipost.net/rss/"),
    ("산업제조", "디스플레이데일리", "displaydaily.co.kr", "https://www.displaydaily.co.kr/rss/"),
    ("산업제조", "배터리인사이드",   "batteryinside.co.kr","https://www.batteryinside.co.kr/feed"),
    # 제약·바이오·의료
    ("제약바이오", "약업신문",       "yakup.com",          "https://www.yakup.com/rss/"),
    ("제약바이오", "히트뉴스",       "hitnews.co.kr",      "https://www.hitnews.co.kr/rss/"),
    ("제약바이오", "바이오스펙테이터","biospectator.com",  "https://biospectator.com/rss/"),
    ("제약바이오", "팜뉴스",         "pharmnews.com",      "https://www.pharmnews.com/rss/"),
    ("제약바이오", "메디파나뉴스",   "medipana.com",       "https://www.medipana.com/rss/"),
    ("제약바이오", "바이오타임즈",   "biotimes.co.kr",     "https://www.biotimes.co.kr/rss/"),
    ("제약바이오", "청년의사",       "docdocdoc.co.kr",    "https://www.docdocdoc.co.kr/rss/"),
    ("제약바이오", "의약뉴스",       "newsmp.com",         "https://www.newsmp.com/rss/"),
    # 식품·화장품·생활
    ("식품화장품", "식품음료신문", "thinkfood.co.kr",     "https://www.thinkfood.co.kr/rss/"),
    ("식품화장품", "한국식품신문", "kfoodtimes.com",      "https://www.kfoodtimes.com/rss/"),
    ("식품화장품", "식품저널",     "foodnews.co.kr",      "https://www.foodnews.co.kr/rss/"),
    ("식품화장품", "코스모닝",     "cosmorning.com",      "https://www.cosmorning.com/rss/"),
    ("식품화장품", "코스인코리아", "cosinkorea.com",      "https://www.cosinkorea.com/rss/"),
    ("식품화장품", "뷰티경제",     "beautyeconomy.co.kr", "https://www.beautyeconomy.co.kr/rss/"),
    # 자동차·모빌리티
    ("자동차", "오토데일리",  "autodaily.co.kr",  "https://www.autodaily.co.kr/rss/"),
    ("자동차", "오토타임즈",  "autotimes.co.kr",  "https://www.autotimes.co.kr/rss/"),
    ("자동차", "타이어프레스","tyrpress.com",     "https://www.tyrpress.com/rss/"),
    ("자동차", "카가이",      "carguy.kr",        "https://www.carguy.kr/rss/"),
    ("자동차", "모터트렌드",  "motortrend.co.kr", "https://www.motortrend.co.kr/rss/"),
    # 건설·부동산·건축
    ("건설부동산", "건설경제신문",    "cnews.co.kr",      "https://www.cnews.co.kr/rss/"),
    ("건설부동산", "건설타임즈",      "constimes.co.kr",  "https://www.constimes.co.kr/rss/"),
    ("건설부동산", "대한전문건설신문","koscaj.or.kr",     "https://www.koscaj.or.kr/rss/"),
    ("건설부동산", "부동산114",       "r114.com",         "https://www.r114.com/rss/"),
    ("건설부동산", "아파트관리신문",  "aptn.co.kr",       "https://www.aptn.co.kr/rss/"),
    # 에너지·환경
    ("에너지환경", "전기신문",       "electimes.com",    "https://www.electimes.com/rss/"),
    ("에너지환경", "가스신문",       "gasnews.com",      "https://www.gasnews.com/rss/"),
    ("에너지환경", "에너지경제신문", "ekn.kr",           "https://www.ekn.kr/rss/"),
    ("에너지환경", "환경일보",       "hkbs.co.kr",       "https://www.hkbs.co.kr/rss/"),
    # 지역신문
    ("지역", "경기일보",     "kyeonggi.com",     "https://www.kyeonggi.com/rss/"),
    ("지역", "경인일보",     "kyeongin.com",     "https://www.kyeongin.com/rss/"),
    ("지역", "수원일보",     "suwon.com",        "https://www.suwon.com/rss/"),
    ("지역", "인천일보",     "incheonilbo.com",  "https://www.incheonilbo.com/rss/"),
    ("지역", "기호일보",     "kihoilbo.co.kr",   "https://www.kihoilbo.co.kr/rss/"),
    ("지역", "대전일보",     "daejonilbo.com",   "https://www.daejonilbo.com/rss/"),
    ("지역", "충청투데이",   "cctoday.co.kr",    "https://www.cctoday.co.kr/rss/"),
    ("지역", "충북일보",     "inews365.com",     "https://www.inews365.com/rss/"),
    ("지역", "중도일보",     "joongdo.co.kr",    "https://www.joongdo.co.kr/rss/"),
    ("지역", "경북신문",     "kbsm.net",         "https://www.kbsm.net/rss/"),
    ("지역", "경북매일",     "kbmaeil.com",      "https://www.kbmaeil.com/rss/"),
    ("지역", "울산매일",     "iusm.co.kr",       "https://www.iusm.co.kr/rss/"),
    ("지역", "경상일보",     "ksilbo.co.kr",     "https://www.ksilbo.co.kr/rss/"),
    ("지역", "부산일보",     "busan.com",        "https://www.busan.com/rss/"),
    ("지역", "국제신문",     "kookje.co.kr",     "https://www.kookje.co.kr/rss/"),
    ("지역", "전북일보",     "jjan.kr",          "https://www.jjan.kr/rss/"),
    ("지역", "광주일보",     "kwangju.co.kr",    "https://www.kwangju.co.kr/rss/"),
    ("지역", "강원일보",     "kwnews.co.kr",     "https://www.kwnews.co.kr/rss/"),
]


@dataclass
class ValidationResult:
    category: str
    name: str
    domain: str
    url: str
    status: str = "❌"      # ✅ / ⚠️ / ❌
    http_code: int | None = None
    entry_count: int = 0
    latest_dt: datetime | None = None
    sample_title: str = ""
    error: str = ""
    elapsed_ms: int = 0


def _fetch(url: str, verify_ssl: bool) -> tuple[int | None, str | None, str]:
    """HTTP fetch with verify=False fallback. returns (status_code, body, error)."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.9, */*;q=0.5",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
    }
    try:
        r = requests.get(url, headers=headers, timeout=TIMEOUT, verify=verify_ssl, allow_redirects=True)
        if r.encoding and r.encoding.lower() in ("iso-8859-1", "ascii"):
            r.encoding = r.apparent_encoding or "utf-8"
        return r.status_code, r.text, ""
    except requests.exceptions.SSLError as e:
        if verify_ssl:
            # 사내 SSL inspection 가능성 → verify=False 재시도
            try:
                r = requests.get(url, headers=headers, timeout=TIMEOUT, verify=False, allow_redirects=True)
                if r.encoding and r.encoding.lower() in ("iso-8859-1", "ascii"):
                    r.encoding = r.apparent_encoding or "utf-8"
                return r.status_code, r.text, ""
            except requests.RequestException as e2:
                return None, None, f"SSL & retry fail: {type(e2).__name__}"
        return None, None, f"SSL: {type(e).__name__}"
    except requests.exceptions.ConnectionError as e:
        return None, None, f"Conn: {str(e)[:80]}"
    except requests.exceptions.Timeout:
        return None, None, "Timeout"
    except requests.RequestException as e:
        return None, None, f"{type(e).__name__}: {str(e)[:60]}"


def validate(cand: tuple[str, str, str, str], verify_ssl: bool) -> ValidationResult:
    category, name, domain, url = cand
    res = ValidationResult(category=category, name=name, domain=domain, url=url)
    t0 = time.time()

    code, body, err = _fetch(url, verify_ssl)
    res.http_code = code
    if body is None:
        res.error = err or "no body"
        res.elapsed_ms = int((time.time() - t0) * 1000)
        return res

    if code and code >= 400:
        res.error = f"HTTP {code}"
        res.elapsed_ms = int((time.time() - t0) * 1000)
        return res

    # feedparser 파싱
    try:
        parsed = feedparser.parse(body)
    except Exception as e:
        res.error = f"parse: {type(e).__name__}"
        res.elapsed_ms = int((time.time() - t0) * 1000)
        return res

    entries = parsed.entries or []
    res.entry_count = len(entries)

    if not entries:
        # bozo + HTML 응답인지 체크
        if "<html" in body[:500].lower():
            res.error = "HTML 응답 (RSS 아님)"
        elif parsed.bozo:
            res.error = f"bozo: {type(parsed.bozo_exception).__name__ if parsed.bozo_exception else 'unknown'}"
        else:
            res.error = "0 entries"
        res.elapsed_ms = int((time.time() - t0) * 1000)
        return res

    # 샘플 + 최근 시각
    first = entries[0]
    res.sample_title = (first.get("title") or "")[:60]

    # 최근 게시 시각
    latest = None
    for e in entries[:20]:
        for key in ("published_parsed", "updated_parsed"):
            st = e.get(key)
            if st:
                try:
                    dt = datetime(*st[:6], tzinfo=timezone.utc).astimezone(KST)
                    if not latest or dt > latest:
                        latest = dt
                except Exception:
                    pass
        if latest is None:
            for key in ("published", "updated"):
                s = e.get(key)
                if s:
                    try:
                        dt = dateparser.parse(s).astimezone(KST)
                        if not latest or dt > latest:
                            latest = dt
                    except Exception:
                        pass
    res.latest_dt = latest

    # 상태 결정
    seven_days_ago = datetime.now(KST) - timedelta(days=7)
    if latest and latest >= seven_days_ago:
        res.status = "✅"
    elif res.entry_count > 0:
        res.status = "⚠️"  # 살아있는데 최근 글이 없음
    else:
        res.status = "❌"

    res.elapsed_ms = int((time.time() - t0) * 1000)
    return res


def write_report(results: list[ValidationResult], output: Path) -> None:
    ok = [r for r in results if r.status == "✅"]
    warn = [r for r in results if r.status == "⚠️"]
    bad = [r for r in results if r.status == "❌"]

    lines: list[str] = []
    lines.append(f"# RSS 피드 검증 리포트")
    lines.append("")
    lines.append(f"- 검증 시각: {datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')}")
    lines.append(f"- 총 {len(results)}개 / ✅ {len(ok)} / ⚠️ {len(warn)} / ❌ {len(bad)}")
    lines.append("")
    lines.append("## ✅ 정상 (최근 7일 내 기사 있음)")
    lines.append("")
    lines.append("| # | 분류 | 매체 | 도메인 | URL | 최근글 | 건수 | 샘플 제목 |")
    lines.append("|---|------|------|--------|-----|--------|------|-----------|")
    for i, r in enumerate(ok, 1):
        latest = r.latest_dt.strftime("%m-%d %H:%M") if r.latest_dt else "-"
        lines.append(f"| {i} | {r.category} | {r.name} | {r.domain} | {r.url} | {latest} | {r.entry_count} | {r.sample_title} |")
    lines.append("")
    lines.append("## ⚠️ 살아있지만 최근글 없음 / 메타 이상")
    lines.append("")
    lines.append("| # | 분류 | 매체 | URL | 건수 | 최근글 | 메모 |")
    lines.append("|---|------|------|-----|------|--------|------|")
    for i, r in enumerate(warn, 1):
        latest = r.latest_dt.strftime("%Y-%m-%d") if r.latest_dt else "-"
        lines.append(f"| {i} | {r.category} | {r.name} | {r.url} | {r.entry_count} | {latest} | {r.error or '-'} |")
    lines.append("")
    lines.append("## ❌ 실패 (재탐색 필요)")
    lines.append("")
    lines.append("| # | 분류 | 매체 | 도메인 | URL | HTTP | 사유 |")
    lines.append("|---|------|------|--------|-----|------|------|")
    for i, r in enumerate(bad, 1):
        lines.append(f"| {i} | {r.category} | {r.name} | {r.domain} | {r.url} | {r.http_code or '-'} | {r.error} |")
    lines.append("")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    logger.info("리포트 저장: %s", output)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--insecure", action="store_true", help="SSL 검증 끄기")
    ap.add_argument("--workers", type=int, default=10, help="동시 요청 수")
    ap.add_argument("--output", default=None, help="출력 경로 (생략 시 data/rss_validation_{date}.md)")
    args = ap.parse_args()

    if args.insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    else:
        # 어차피 _fetch 내부에서 verify=False 폴백을 쓰니까 경고는 다 꺼버린다
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    logger.info("RSS 검증 시작: %d개, workers=%d, verify_ssl=%s",
                len(CANDIDATES), args.workers, not args.insecure)

    results: list[ValidationResult] = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(validate, c, not args.insecure): c for c in CANDIDATES}
        for i, fut in enumerate(as_completed(futures), 1):
            r = fut.result()
            results.append(r)
            logger.info("  [%3d/%d] %s %-18s %s",
                        i, len(CANDIDATES), r.status, r.name, r.error or r.sample_title[:50])

    # 카테고리/이름 순으로 정렬
    results.sort(key=lambda r: (r.category, r.name))

    if args.output:
        output = Path(args.output)
    else:
        date = datetime.now(KST).strftime("%Y-%m-%d")
        output = Path(f"data/rss_validation_{date}.md")

    write_report(results, output)

    ok = sum(1 for r in results if r.status == "✅")
    warn = sum(1 for r in results if r.status == "⚠️")
    bad = sum(1 for r in results if r.status == "❌")
    elapsed = time.time() - t0
    logger.info("─" * 60)
    logger.info("완료: %d개 / ✅ %d / ⚠️ %d / ❌ %d (%.1fs)",
                len(results), ok, warn, bad, elapsed)


if __name__ == "__main__":
    main()
