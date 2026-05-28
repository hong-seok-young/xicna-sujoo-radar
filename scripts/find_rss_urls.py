"""HTML 응답을 줬던 14개 매체에서 실제 RSS URL을 추출.

전략:
1. 우리가 시도한 URL + 도메인별 흔한 RSS 안내 경로(/rss/, /rss, /rss/rssList.html, ...)를 차례로 GET
2. 응답에서 .xml / /rss / /feed 패턴의 링크를 BS4로 추출
3. 추출된 후보 URL을 feedparser 로 다시 검증 → entry 1개 이상이면 ✅
4. 매체별로 가장 entry 많은 ✅ URL을 최종 후보로 출력

이 스크립트는 의사결정을 자동화하지 않는다. 결과를 출력만 하고,
어떤 URL을 config 에 넣을지는 사람이 본 다음 골라서 적용한다.

사용법:
    python scripts/find_rss_urls.py
"""
from __future__ import annotations

import logging
import re
import time
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = 12

# HTML 응답을 줬던 14개 매체.
# 각 매체별로 [원래 시도한 URL, 도메인별 흔한 안내 경로...] 를 try 순서로 나열.
TARGETS: dict[str, list[str]] = {
    "조선일보": [
        "https://rssplus.chosun.com/",
        "https://www.chosun.com/site/data/rss/rss.xml",
        "https://www.chosun.com/arc/outboundfeeds/rss/?outputType=xml",
    ],
    "중앙일보": [
        "https://rss.joins.com/joins_news_list.xml",
        "https://www.joongang.co.kr/rss",
        "https://www.joongang.co.kr/sitemap.xml",
    ],
    "한국경제": [
        "http://rss.hankyung.com/feed/economy.xml",
        "http://rss.hankyung.com/",
        "https://www.hankyung.com/rss",
    ],
    "연합뉴스": [
        "https://www.yonhapnews.co.kr/rss/index.xml",
        "https://www.yna.co.kr/rss",
        "https://www.yna.co.kr/news/rss",
    ],
    "뉴스1": [
        "https://www.news1.kr/rss/allnews.xml",
        "https://www.news1.kr/rss",
    ],
    "뉴시스": [
        "https://www.newsis.com/RSS/allnews.xml",
        "https://www.newsis.com/rss/",
        "https://newsis.com/rss/",
    ],
    "SBS뉴스": [
        "https://news.sbs.co.kr/news/rss.do",
        "https://news.sbs.co.kr/news/newsList.do?plink=GNB&cooper=SBSNEWS",
    ],
    "MBC뉴스": [
        "https://imnews.imbc.com/rss/news/news_00.xml",
        "https://imnews.imbc.com/rss/",
    ],
    "이투데이": [
        "https://www.etoday.co.kr/rss/",
        "https://www.etoday.co.kr/rss",
    ],
    "아이씨엔매거진": [
        "https://www.icnweb.co.kr/rss/",
        "https://www.icnweb.co.kr/rss",
    ],
    "경북신문": [
        "https://www.kbsm.net/rss/",
        "https://www.kbsm.net/rss",
    ],
    "전북일보": [
        "https://www.jjan.kr/rss/",
        "https://www.jjan.kr/rss",
    ],
    "국제신문": [
        "https://www.kookje.co.kr/rss/",
        "https://www.kookje.co.kr/rss",
    ],
    "강원일보": [
        "https://www.kwnews.co.kr/rss/",
        "https://www.kwnews.co.kr/rss",
    ],
}


@dataclass
class Candidate:
    url: str
    entry_count: int = 0
    sample_title: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.entry_count > 0


@dataclass
class MediaResult:
    name: str
    pages_tried: list[str] = field(default_factory=list)
    candidates_extracted: int = 0
    valid_feeds: list[Candidate] = field(default_factory=list)


def fetch(url: str) -> tuple[int | None, str | None, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html, application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
        "Referer": "https://www.google.com/",
    }
    try:
        r = requests.get(url, headers=headers, timeout=TIMEOUT, verify=False, allow_redirects=True)
        if r.encoding and r.encoding.lower() in ("iso-8859-1", "ascii"):
            r.encoding = r.apparent_encoding or "utf-8"
        return r.status_code, r.text, ""
    except requests.RequestException as e:
        return None, None, f"{type(e).__name__}"


# RSS 후보 URL 패턴 (느슨하게)
RSS_HINT = re.compile(r"(\.xml($|\?)|/rss(/|$|\.)|/feed(/|$|\.)|rss[a-zA-Z]*\.do)", re.IGNORECASE)


def extract_rss_links(html: str, base_url: str) -> set[str]:
    """HTML 페이지에서 RSS 후보 URL 추출."""
    candidates: set[str] = set()
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return candidates

    # <a>, <link>, <area> 의 href
    for tag in soup.find_all(["a", "link", "area"]):
        href = tag.get("href")
        if href and RSS_HINT.search(href):
            url = urljoin(base_url, href.strip())
            # fragment 제거
            if "#" in url:
                url = url.split("#", 1)[0]
            candidates.add(url)

    # 본문 텍스트 안에도 URL 형태로 RSS가 적혀있을 수 있음
    text_urls = re.findall(r"https?://[^\s\"'<>]+\.xml[^\s\"'<>]*", html, re.IGNORECASE)
    for u in text_urls:
        candidates.add(u)

    return candidates


def validate_feed(url: str) -> Candidate:
    """RSS 후보 URL 1개 검증."""
    c = Candidate(url=url)
    code, body, err = fetch(url)
    if body is None:
        c.error = err or "no body"
        return c
    if code and code >= 400:
        c.error = f"HTTP {code}"
        return c
    try:
        parsed = feedparser.parse(body)
    except Exception as e:
        c.error = f"parse: {type(e).__name__}"
        return c
    entries = parsed.entries or []
    c.entry_count = len(entries)
    if entries:
        c.sample_title = (entries[0].get("title") or "")[:60]
    elif "<html" in body[:500].lower():
        c.error = "HTML"
    elif parsed.bozo:
        c.error = "bozo"
    else:
        c.error = "0 entries"
    return c


def process_media(name: str, anchor_urls: list[str]) -> MediaResult:
    """매체 1개 처리: 안내 페이지들 → 후보 추출 → 검증."""
    res = MediaResult(name=name)
    all_candidates: set[str] = set()

    for anchor in anchor_urls:
        res.pages_tried.append(anchor)
        # anchor 자체가 RSS 일 수도 있으니 먼저 검증해서 ✅면 그것도 후보에 추가
        all_candidates.add(anchor)

        code, body, err = fetch(anchor)
        if body is None:
            continue
        # anchor 가 RSS XML 인 경우 추가 추출은 의미 없음. 그래도 어차피 검증에서 잡힘.
        extracted = extract_rss_links(body, anchor)
        all_candidates.update(extracted)

    res.candidates_extracted = len(all_candidates)

    # 도메인 매칭하는 것만 검증 (외부 사이트 RSS 거름)
    domain_root = urlparse(anchor_urls[0]).netloc
    # 메인 도메인 (sub.example.com → example.com)
    parts = domain_root.split(".")
    main_domain = ".".join(parts[-2:]) if len(parts) > 1 else domain_root

    filtered = [
        u for u in all_candidates
        if main_domain in urlparse(u).netloc
    ]

    # 너무 많으면 짧은 URL부터 우선 (보통 더 일반적인 카테고리)
    filtered.sort(key=lambda u: (len(u), u))
    filtered = filtered[:25]  # 매체당 최대 25개만 검증

    # 병렬 검증
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(validate_feed, u): u for u in filtered}
        for fut in as_completed(futs):
            c = fut.result()
            if c.ok:
                res.valid_feeds.append(c)

    # entry_count 많은 것부터
    res.valid_feeds.sort(key=lambda c: -c.entry_count)
    return res


def main():
    logger.info("HTML 응답 14개 매체에서 실제 RSS URL 자동 탐색")
    t0 = time.time()

    results: list[MediaResult] = []
    # 매체별 직렬 처리 (각 매체 내부에서만 병렬). 사이트 부담 줄이려고.
    for name, anchors in TARGETS.items():
        logger.info("─" * 60)
        logger.info("[%s] 안내 페이지 %d개 시도", name, len(anchors))
        res = process_media(name, anchors)
        logger.info("  → 후보 %d개 추출 / 검증 통과 %d개",
                    res.candidates_extracted, len(res.valid_feeds))
        for c in res.valid_feeds[:5]:
            logger.info("    ✅ %s  (%d건) %s", c.url, c.entry_count, c.sample_title)
        results.append(res)

    # 최종 리포트
    print()
    print("=" * 80)
    print("최종 결과: 매체별 추천 RSS URL (entry 가장 많은 것)")
    print("=" * 80)
    found = 0
    not_found = 0
    for r in results:
        if r.valid_feeds:
            found += 1
            best = r.valid_feeds[0]
            print(f"  ✅ {r.name:<14} {best.url}")
            print(f"     건수={best.entry_count}  샘플='{best.sample_title}'")
            if len(r.valid_feeds) > 1:
                print(f"     (대체 {len(r.valid_feeds)-1}개 더 있음)")
        else:
            not_found += 1
            print(f"  ❌ {r.name:<14} 자동 탐색 실패 ({r.candidates_extracted}개 후보 모두 거름)")
    print("=" * 80)
    print(f"총 {len(results)}개 매체 / ✅ {found} / ❌ {not_found} ({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()
