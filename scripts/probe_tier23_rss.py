"""tier 2/3 차단 매체들의 RSS URL 패턴 brute-force.

rss_feeds.yaml 에서 주석 처리된 매체들(전자신문·디일렉·약업신문 등) 의
다양한 URL 패턴을 시도해서 살아있는 거 찾기.

2026-05-26 검증 시 히트뉴스·식품음료신문이 /rss/allArticle.xml 패턴으로
성공했으므로 같은 패턴 + 흔한 후보들 시도.

사용:
    python scripts/probe_tier23_rss.py
"""
from __future__ import annotations

import logging
import time
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed

import feedparser
import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = 12

# 차단된 매체별 다양한 RSS URL 후보
PROBES: dict[str, list[str]] = {
    # ───── 반도체·디스플레이·배터리 ─────
    "전자신문": [
        "https://www.etnews.com/rss/allnews.xml",
        "https://www.etnews.com/rss/economy.xml",
        "https://www.etnews.com/rss/industry.xml",
        "https://www.etnews.com/rss/etnews.xml",
        "https://www.etnews.com/rss",
        "https://www.etnews.com/feed",
        "https://www.etnews.com/feed/",
        "https://rss.etnews.com/Section901.xml",
        "https://rss.etnews.com/Section902.xml",
        "https://rss.etnews.com/Section903.xml",
        "https://rss.etnews.com/news.xml",
    ],
    "디일렉": [
        "https://www.thelec.kr/rss/allArticle.xml",
        "https://www.thelec.kr/rss/news.xml",
        "https://www.thelec.kr/feed",
        "https://www.thelec.kr/feed/",
        "https://www.thelec.kr/rss.xml",
        "https://thelec.kr/rss/allArticle.xml",
        "https://thelec.kr/feed",
        "https://www.thelec.co.kr/rss/allArticle.xml",  # 옛 도메인 가능성
    ],
    "반도체신문": [
        "https://www.semiconnews.kr/rss/allArticle.xml",
        "https://www.semicon.co.kr/rss/allArticle.xml",
        "https://www.semicon.co.kr/feed",
        "https://www.semiconductor-news.com/feed",
        "https://semicon.co.kr/rss/allArticle.xml",
    ],
    "디스플레이데일리": [
        "https://www.displaydaily.co.kr/rss/allArticle.xml",
        "https://www.ddaily.co.kr/rss/allArticle.xml",
        "https://www.ddaily.co.kr/rss/news.xml",
        "https://ddaily.co.kr/feed",
    ],
    "배터리인사이드": [
        "https://www.batteryinside.co.kr/rss/allArticle.xml",
        "https://www.batteryinside.com/feed",
        "https://www.batteryinside.co.kr/feed",
    ],

    # ───── 제약·바이오 ─────
    "약업신문": [
        "https://www.yakup.com/rss/allArticle.xml",
        "https://www.yakup.com/rss/",
        "https://www.yakup.com/feed",
        "https://www.yakup.com/rss/news.xml",
        "https://www.yakup.com/rss.xml",
        "https://www.yakup.com/rss/news_pharm.xml",
        "http://www.yakup.com/rss.xml",
    ],
    "팜뉴스": [
        "https://www.pharmnews.com/rss/allArticle.xml",
        "https://www.pharmnews.com/rss/",
        "https://www.pharmnews.com/feed",
        "https://pharmnews.com/rss/allArticle.xml",
    ],
    "의약뉴스": [
        "https://www.newsmp.com/rss/allArticle.xml",
        "https://www.newsmp.com/feed",
    ],
    "데일리팜": [
        "https://www.dailypharm.com/rss/allArticle.xml",
        "https://www.dailypharm.com/feed",
        "https://www.dailypharm.com/Users/Rss.xml",
    ],
    "메디소비자뉴스": [
        "https://www.medisobizanews.com/rss/allArticle.xml",
        "https://www.medisobizanews.com/feed",
    ],

    # ───── 식품·화장품 ─────
    "코스모닝": [
        "https://www.cosmorning.com/rss/allArticle.xml",
        "https://www.cosmorning.com/feed",
        "https://www.cosmorning.com/rss/",
    ],
    "장업신문": [
        "https://www.jangup.com/rss/allArticle.xml",
        "https://www.jangup.com/feed",
    ],
    "뷰티누리": [
        "https://www.beautynury.com/rss/allArticle.xml",
        "https://www.beautynury.com/feed",
    ],
    "식품외식경제": [
        "https://www.foodbank.co.kr/rss/allArticle.xml",
        "https://www.foodbank.co.kr/feed",
    ],
    "농수축산신문": [
        "https://www.aflnews.co.kr/rss/allArticle.xml",
        "https://www.aflnews.co.kr/feed",
    ],

    # ───── 건설·플랜트 ─────
    "건설경제신문": [
        "https://www.cnews.co.kr/rss/allArticle.xml",
        "https://www.cnews.co.kr/feed",
        "https://www.cnews.co.kr/rss/news.xml",
        "https://www.cnews.co.kr/rss.xml",
    ],
    "대한경제(건설)": [
        "https://www.dnews.co.kr/rss/allArticle.xml",
        "https://www.dnews.co.kr/feed",
        "https://www.dnews.co.kr/rss/news.xml",
    ],
    "건설기술인": [
        "https://www.ceng.co.kr/rss/allArticle.xml",
        "https://www.ceng.co.kr/feed",
    ],
    "한국건설신문": [
        "https://www.conslove.co.kr/rss/allArticle.xml",
        "https://www.conslove.co.kr/feed",
    ],

    # ───── 산업·종합 ─────
    "산업일보": [
        "https://www.kidd.co.kr/rss/allArticle.xml",
        "https://www.kidd.co.kr/feed",
        "https://www.kidd.co.kr/rss/news.xml",
    ],
    "에너지경제": [
        "https://www.ekn.kr/rss/allArticle.xml",
        "https://www.ekn.kr/feed",
    ],
    "에너지데일리": [
        "https://www.energydaily.co.kr/rss/allArticle.xml",
        "https://www.energydaily.co.kr/feed",
    ],
    "전기신문": [
        "https://www.electimes.com/rss/allArticle.xml",
        "https://www.electimes.com/feed",
    ],
    "투데이에너지": [
        "https://www.todayenergy.kr/rss/allArticle.xml",
        "https://www.todayenergy.kr/feed",
    ],
    "산업뉴스": [
        "https://www.industrynews.co.kr/rss/allArticle.xml",
        "https://www.industrynews.co.kr/feed",
    ],

    # ───── 추가 산업 전문지 (가능성 있는 곳) ─────
    "전자부품": [
        "https://www.epnc.co.kr/rss/allArticle.xml",
        "https://www.epnc.co.kr/feed",
    ],
    "지디넷코리아": [
        "https://feeds.zdnet.co.kr/news/today.xml",
        "https://www.zdnet.co.kr/rss/today.xml",
        "https://zdnet.co.kr/rss/allnews.xml",
    ],
    "데일리한국": [
        "https://daily.hankooki.com/rss/allArticle.xml",
        "https://daily.hankooki.com/feed",
    ],
    "아주경제": [
        "https://www.ajunews.com/rss/allArticle.xml",
        "https://www.ajunews.com/feed",
        "https://www.ajunews.com/rss/news.xml",
        "https://www.ajunews.com/rss/economy.xml",
        "https://www.ajunews.com/rss/industry.xml",
    ],
    "헤럴드경제": [
        "https://biz.heraldcorp.com/rss/allArticle.xml",
        "https://news.heraldcorp.com/rss/allArticle.xml",
        "https://www.heraldcorp.com/rss/allArticle.xml",
        "https://biz.heraldcorp.com/feed",
    ],
    "서울경제": [
        "https://www.sedaily.com/RSS/S1N1.xml",
        "https://www.sedaily.com/rss/allArticle.xml",
        "https://www.sedaily.com/feed",
        "https://www.sedaily.com/RSS/Economy.xml",
    ],
    "머니투데이": [
        "https://rss.mt.co.kr/mt_news.xml",
        "https://news.mt.co.kr/mtview.php?type=rss",
        "https://www.mt.co.kr/rss/allArticle.xml",
        "https://www.mt.co.kr/feed",
    ],
    "더벨": [
        "https://www.thebell.co.kr/rss/allArticle.xml",
        "https://www.thebell.co.kr/feed",
    ],
}


def probe(url: str) -> tuple[str, bool, int, str]:
    """returns (url, ok, entry_count, sample_title_or_error)."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
        "Referer": "https://www.google.com/",
    }
    try:
        r = requests.get(url, headers=headers, timeout=TIMEOUT, verify=False, allow_redirects=True)
        if r.encoding and r.encoding.lower() in ("iso-8859-1", "ascii"):
            r.encoding = r.apparent_encoding or "utf-8"
        if r.status_code >= 400:
            return url, False, 0, f"HTTP {r.status_code}"
        parsed = feedparser.parse(r.text)
        entries = parsed.entries or []
        if entries:
            sample = (entries[0].get("title") or "")[:50]
            return url, True, len(entries), sample
        if "<html" in r.text[:500].lower():
            return url, False, 0, "HTML"
        return url, False, 0, "0 entries"
    except requests.exceptions.SSLError:
        return url, False, 0, "SSL"
    except requests.exceptions.ConnectionError as e:
        msg = str(e)
        if "getaddrinfo" in msg or "Name or service" in msg:
            return url, False, 0, "DNS"
        return url, False, 0, "Conn"
    except requests.exceptions.Timeout:
        return url, False, 0, "Timeout"
    except Exception as e:
        return url, False, 0, type(e).__name__


def main():
    logger.info("tier 2/3 차단 매체 RSS URL brute-force")
    t0 = time.time()

    all_jobs: list[tuple[str, str]] = []
    for media, urls in PROBES.items():
        for u in urls:
            all_jobs.append((media, u))
    logger.info("총 %d개 URL 시도 (매체 %d개)", len(all_jobs), len(PROBES))

    results_by_media: dict[str, list[tuple[str, bool, int, str]]] = {m: [] for m in PROBES}

    with ThreadPoolExecutor(max_workers=15) as pool:
        futs = {pool.submit(probe, u): (m, u) for m, u in all_jobs}
        done_n = 0
        for fut in as_completed(futs):
            done_n += 1
            url, ok, n, info = fut.result()
            media, _ = futs[fut]
            results_by_media[media].append((url, ok, n, info))
            mark = "✅" if ok else "  "
            if ok:
                logger.info("  [%3d/%d] %s %-20s %s  →  %d건  '%s'",
                            done_n, len(all_jobs), mark, media, url, n, info)

    print()
    print("=" * 80)
    print("매체별 결과 (✅ = 사용 가능한 RSS URL)")
    print("=" * 80)
    found_total = 0
    for media in PROBES:
        rows = results_by_media[media]
        ok_rows = [r for r in rows if r[1]]
        if ok_rows:
            found_total += 1
            print(f"\n[{media}] ✅ {len(ok_rows)}/{len(rows)}개 작동")
            for url, _, n, info in sorted(ok_rows, key=lambda x: -x[2]):
                print(f"  ✅ {n:>3d}건  {url}")
                print(f"        샘플: '{info}'")
        else:
            reasons = [r[3] for r in rows]
            # 가장 흔한 실패 사유
            from collections import Counter
            top_reason = Counter(reasons).most_common(1)[0][0] if reasons else "?"
            print(f"\n[{media}] ❌ 모두 실패 ({len(rows)}회 / 주된 사유: {top_reason})")

    print()
    print("=" * 80)
    print(f"총 {len(PROBES)}개 매체 / ✅ 신규 발견 {found_total}개 ({time.time()-t0:.1f}s)")
    print()
    print("=== rss_feeds.yaml 에 추가할 yaml 스니펫 ===")
    for media in PROBES:
        rows = results_by_media[media]
        best = [r for r in rows if r[1]]
        if not best:
            continue
        best.sort(key=lambda x: -x[2])
        url = best[0][0]
        # 도메인 추출
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.replace("www.", "")
        print(f'  - name: "{media}"')
        print(f'    url: "{url}"')
        print(f'    tier: 2')
        print(f'    domain: "{domain}"')


if __name__ == "__main__":
    main()
