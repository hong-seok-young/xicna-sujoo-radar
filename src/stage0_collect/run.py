"""Stage 0: 수집기 — RSS 기반 (1단계 구현).

config/rss_feeds.yaml 의 tier 매칭 피드를 feedparser로 파싱해서
최근 N일치 기사를 data/raw/{YYYY-MM-DD}.jsonl 로 저장한다.

사용법:
    python -m src.stage0_collect.run --tier 1 --days 7
    python -m src.stage0_collect.run --tier 1 --days 7 --output data/raw/test.jsonl

설계 메모:
- content 는 RSS summary/description 으로 채움 (본문 전체 fetch는 다음 단계).
- ID 는 URL 의 md5 해시 (CLAUDE.md §5 멱등성).
- 피드 1개 실패해도 전체 파이프라인은 안 멈춤 (개별 try/except).
- BS4 로 HTML 태그 제거해서 룰 필터가 깨끗한 텍스트로 매칭하게 함.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

import feedparser
import requests
import urllib3
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from ..common.config import rss_feeds
from ..common.io import write_jsonl
from ..common.schema import Article

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 sujoo-radar/0.1"
REQUEST_TIMEOUT = 15


def _make_id(url: str) -> str:
    """URL md5 → 멱등 ID."""
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:16]


def _clean_html(text: str) -> str:
    """RSS summary 안의 HTML 태그·엔티티 제거."""
    if not text:
        return ""
    return BeautifulSoup(text, "lxml").get_text(separator=" ", strip=True)


def _parse_dt(entry) -> datetime | None:
    """feedparser entry 에서 published_at 추출 (KST).

    중요 — feedparser 의 published_parsed(struct_time) 는 타임존 없는(naive) pubDate 를
    UTC 로 가정한다. 한국 매체 상당수(hitnews 등)는 KST 시각을 타임존 표기 없이 송출하므로,
    그대로 UTC→KST 변환하면 +9h 밀려 오후 기사가 다음날로 넘어간다.
    → 원본 문자열을 먼저 파싱해서 naive 면 KST 로 간주, 타임존이 명시돼 있으면 그 값을 신뢰.
    """
    for key in ("published", "updated"):
        s = entry.get(key)
        if not s:
            continue
        try:
            dt = dateparser.parse(s)
        except (ValueError, TypeError, OverflowError):
            dt = None
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=KST)   # 타임존 없는 한국 매체 시각 → KST 간주
            return dt.astimezone(KST)
    # 백업: 구조화 파싱 (원본 문자열이 없거나 dateutil 이 못 읽을 때만)
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st:
            try:
                return datetime(*st[:6], tzinfo=timezone.utc).astimezone(KST)
            except (TypeError, ValueError):
                pass
    return None


def _domain(url: str) -> str:
    return urlparse(url).netloc.replace("www.", "")


def _fetch_text(url: str, verify_ssl: bool) -> str | None:
    """requests 로 RSS 본문 가져오기. verify_ssl=False 면 사내 SSL 우회."""
    try:
        r = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml, */*"},
            timeout=REQUEST_TIMEOUT,
            verify=verify_ssl,
        )
        r.raise_for_status()
        # RSS 는 보통 ISO-8859-1 로 잘못 디코딩되므로 직접 결정
        if r.encoding and r.encoding.lower() in ("iso-8859-1", "ascii"):
            r.encoding = r.apparent_encoding or "utf-8"
        return r.text
    except requests.RequestException as e:
        # 1차 실패하고 verify=True 였다면 verify=False 로 재시도
        if verify_ssl:
            try:
                r = requests.get(
                    url,
                    headers={"User-Agent": USER_AGENT},
                    timeout=REQUEST_TIMEOUT,
                    verify=False,
                )
                r.raise_for_status()
                if r.encoding and r.encoding.lower() in ("iso-8859-1", "ascii"):
                    r.encoding = r.apparent_encoding or "utf-8"
                return r.text
            except requests.RequestException as e2:
                logger.warning("HTTP 실패 (verify=False 재시도 후에도): %s", e2)
                return None
        logger.warning("HTTP 실패: %s", e)
        return None


def fetch_feed(feed_cfg: dict, since: datetime, verify_ssl: bool) -> Iterator[Article]:
    """RSS 1개 → Article iterator. 실패해도 yield 0건."""
    name = feed_cfg["name"]
    url = feed_cfg["url"]
    domain_hint = feed_cfg.get("domain") or _domain(url)

    t0 = time.time()
    text = _fetch_text(url, verify_ssl)
    if text is None:
        logger.warning("[%s] 가져오기 실패 → 스킵", name)
        return

    try:
        parsed = feedparser.parse(text)
    except Exception as e:
        logger.warning("[%s] 파싱 실패: %s", name, e)
        return

    if not parsed.entries:
        logger.warning("[%s] entries 0개 (bozo=%s, %s)",
                       name, parsed.bozo, getattr(parsed, "bozo_exception", ""))
        return

    total = len(parsed.entries)
    kept = 0
    skipped_old = 0
    for entry in parsed.entries:
        try:
            link = entry.get("link", "").strip()
            title = (entry.get("title") or "").strip()
            if not link or not title:
                continue
            published = _parse_dt(entry)
            if published and published < since:
                skipped_old += 1
                continue

            summary = entry.get("summary") or entry.get("description") or ""
            content = _clean_html(summary)

            yield Article(
                id=_make_id(link),
                source=domain_hint or _domain(link),
                url=link,
                title=title,
                content=content,
                published_at=published,
            )
            kept += 1
        except Exception as e:
            logger.warning("[%s] 기사 변환 실패: %s", name, e)
            continue

    dt = time.time() - t0
    logger.info("  [%s] %d개 중 %d개 수집 (오래된 %d 제외, %.2fs)",
                name, total, kept, skipped_old, dt)


def collect(tier: int, days: int, verify_ssl: bool = True) -> list[Article]:
    """tier 매칭 피드 전체 수집. 중복 URL 제거."""
    cfg = rss_feeds()
    feeds = [f for f in cfg.get("feeds", []) if f.get("tier") == tier]
    if not feeds:
        logger.warning("Tier %d 피드가 없습니다.", tier)
        return []

    since = datetime.now(KST) - timedelta(days=days)
    logger.info("Tier %d 피드 %d개 / 기준일=%s 이후 (verify_ssl=%s)",
                tier, len(feeds), since.strftime("%Y-%m-%d %H:%M"), verify_ssl)

    seen_ids: set[str] = set()
    articles: list[Article] = []
    for feed_cfg in feeds:
        for article in fetch_feed(feed_cfg, since, verify_ssl):
            if article.id in seen_ids:
                continue
            seen_ids.add(article.id)
            articles.append(article)

    return articles


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", type=int, default=1, choices=[1, 2, 3])
    parser.add_argument("--days", type=int, default=7, help="최근 N일치 (기본 7)")
    parser.add_argument("--output", help="출력 JSONL 경로 (생략 시 data/raw/{date}.jsonl)")
    parser.add_argument("--insecure", action="store_true",
                        help="SSL 검증 비활성화 (사내망 SSL Inspection 환경 대응)")
    args = parser.parse_args()

    if args.insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    articles = collect(tier=args.tier, days=args.days, verify_ssl=not args.insecure)

    if args.output:
        output_path = Path(args.output)
    else:
        today = datetime.now(KST).strftime("%Y-%m-%d")
        output_path = Path(f"data/raw/{today}.jsonl")

    written = write_jsonl(output_path, articles)

    # 매체별 집계
    by_source: dict[str, int] = {}
    for a in articles:
        by_source[a.source] = by_source.get(a.source, 0) + 1

    logger.info("─" * 60)
    logger.info("Stage 0 완료: 총 %d건 수집 → %s", written, output_path)
    logger.info("매체별 분포:")
    for src, n in sorted(by_source.items(), key=lambda x: -x[1]):
        logger.info("  %-30s %4d", src, n)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
