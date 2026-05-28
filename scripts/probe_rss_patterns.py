"""find_rss_urls.py 에서 실패한 매체에 대해 알려진/추측 RSS URL 패턴을 brute-force.

매체별로 사람이 사전에 안다고 가정하는 URL 패턴을 나열하고,
실제로 feedparser 로 검증되는지 확인.

사용법:
    python scripts/probe_rss_patterns.py
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

# 매체별로 알려진/추측되는 RSS URL 후보 (대량)
PROBES: dict[str, list[str]] = {
    "중앙일보": [
        # joongang.co.kr 로 통합. 옛 joins.com RSS 는 polite redirect 가능성
        "https://rss.joins.com/sonagi/joins_homenews_sonagi_total.xml",
        "https://rss.joins.com/joins_news_list.xml",
        "https://www.joongang.co.kr/rss/news.xml",
        "https://www.joongang.co.kr/rss/total.xml",
        "https://www.joongang.co.kr/rss/economy.xml",
        "https://www.joongang.co.kr/rss/politics.xml",
        "https://news.joins.com/rss/Joinsmsn.xml",
    ],
    "연합뉴스": [
        "https://www.yna.co.kr/rss/news.xml",
        "https://www.yna.co.kr/rss/economy.xml",
        "https://www.yna.co.kr/rss/industry.xml",
        "https://www.yna.co.kr/rss/all.xml",
        "https://www.yonhapnews.co.kr/RSS/news.xml",
        "https://www.yonhapnews.co.kr/RSS/economy.xml",
        "https://www.yonhapnewstv.co.kr/category/news/economy/feed",
        "https://www.yna.co.kr/RSS/news.xml",
    ],
    "뉴스1": [
        "https://www.news1.kr/rss/S1N1.xml",
        "https://www.news1.kr/rss/S1N2.xml",
        "https://www.news1.kr/rss/S1N3.xml",
        "https://www.news1.kr/rss/economy.xml",
        "https://www.news1.kr/rss/category/economy",
        "https://news1.kr/rss/economy.xml",
        "https://news1.kr/rss/allnews.xml",
        "https://www.news1.kr/feed",
    ],
    "뉴시스": [
        "https://www.newsis.com/RSS/economy.xml",
        "https://www.newsis.com/RSS/industry.xml",
        "https://www.newsis.com/RSS/society.xml",
        "https://www.newsis.com/RSS/politics.xml",
        "https://www.newsis.com/rss/economy.xml",
        "https://www.newsis.com/rss/industry.xml",
        "https://newsis.com/rss/economy.xml",
    ],
    "MBC뉴스": [
        "https://imnews.imbc.com/rss/news/news_economy.xml",
        "https://imnews.imbc.com/rss/news/news_society.xml",
        "https://imnews.imbc.com/rss/news/news_politics.xml",
        "https://imnews.imbc.com/rss/news/economy.xml",
        "https://imnews.imbc.com/rss/news/01.xml",
        "https://imnews.imbc.com/rss/news/02.xml",
        "https://imnews.imbc.com/rss/news/news_main.xml",
    ],
    "아이씨엔매거진": [
        "https://www.icnweb.co.kr/feed",
        "https://www.icnweb.co.kr/feed/",
        "https://www.icnweb.co.kr/rss.xml",
        "https://www.icnweb.co.kr/?feed=rss2",
        "https://www.icnweb.co.kr/rss/feed.xml",
    ],
    "전북일보": [
        "https://www.jjan.kr/rss/allnews.xml",
        "https://www.jjan.kr/rss/news.xml",
        "https://www.jjan.kr/rss/rss.php",
        "https://www.jjan.kr/feed",
    ],
    "국제신문": [
        "https://www.kookje.co.kr/rss/news.xml",
        "https://www.kookje.co.kr/rss/allnews.xml",
        "https://www.kookje.co.kr/rss/economy.xml",
        "https://www.kookje.co.kr/rss/rss.php",
    ],
    "강원일보": [
        "https://www.kwnews.co.kr/rss/allnews.xml",
        "https://www.kwnews.co.kr/rss/news.xml",
        "https://www.kwnews.co.kr/rss/rss.php",
        "https://www.kwnews.co.kr/feed",
    ],
    # 종합 검증 후 추가하면 좋을 카테고리별 RSS (이미 살아있지만 더 많이 확보)
    "한국경제(산업)": [
        "https://www.hankyung.com/feed/industry",
        "https://www.hankyung.com/feed/realestate",
    ],
    "조선일보(경제·산업)": [
        "https://www.chosun.com/arc/outboundfeeds/rss/category/economy/?outputType=xml",
        "https://www.chosun.com/arc/outboundfeeds/rss/category/national/?outputType=xml",
    ],
}


def probe(url: str) -> tuple[str, bool, int, str]:
    """returns (url, ok, entry_count, sample_or_error)."""
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
            sample = (entries[0].get("title") or "")[:60]
            return url, True, len(entries), sample
        if "<html" in r.text[:500].lower():
            return url, False, 0, "HTML"
        return url, False, 0, "0 entries"
    except requests.exceptions.SSLError:
        return url, False, 0, "SSL"
    except requests.exceptions.ConnectionError:
        return url, False, 0, "Conn"
    except requests.exceptions.Timeout:
        return url, False, 0, "Timeout"
    except Exception as e:
        return url, False, 0, type(e).__name__


def main():
    logger.info("실패 매체에 대해 알려진 RSS URL 패턴 brute-force")
    t0 = time.time()

    # 모든 URL 평탄화
    all_jobs: list[tuple[str, str]] = []
    for media, urls in PROBES.items():
        for u in urls:
            all_jobs.append((media, u))

    logger.info("총 %d개 URL 시도 (매체 %d개)", len(all_jobs), len(PROBES))

    results_by_media: dict[str, list[tuple[str, bool, int, str]]] = {m: [] for m in PROBES}

    with ThreadPoolExecutor(max_workers=15) as pool:
        futs = {pool.submit(probe, u): (m, u) for m, u in all_jobs}
        for i, fut in enumerate(as_completed(futs), 1):
            url, ok, n, info = fut.result()
            media, _ = futs[fut]
            results_by_media[media].append((url, ok, n, info))
            mark = "✅" if ok else "  "
            logger.info("  [%3d/%d] %s %-16s %s  →  %s",
                        i, len(all_jobs), mark, media, url, f"{n}건" if ok else info)

    # 매체별 정리
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
                print(f"  ✅ {url}")
                print(f"     {n}건  '{info}'")
        else:
            # 가장 흔한 실패 원인
            reasons = [r[3] for r in rows]
            print(f"\n[{media}] ❌ 모두 실패")
            for url, _, _, info in rows[:3]:
                print(f"  ❌ {url}  ({info})")

    print()
    print("=" * 80)
    print(f"총 {len(PROBES)}개 매체 / ✅ 신규 발견 {found_total} ({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()
