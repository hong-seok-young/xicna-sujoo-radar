"""Stage 4: 주간 보고서 생성.

- 중복 제거 (같은 사건 여러 매체 보도 → 1건으로 묶기)
- 영업팀 필터 (금액·지역) 적용
- 산업군별 그룹핑
- Markdown / HTML 출력

TODO (Claude Code가 구현):
- 중복 판단 정교화: (client_name + project_name + ±3일) 기반
- HTML 템플릿 (jinja2)
- 이메일 발송 (smtplib)
"""
from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from ..common.config import industries
from ..common.io import read_jsonl

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def deduplicate(articles: list) -> list:
    """중복 제거 (간단 버전: client + project 키로 묶음)."""
    seen: dict[tuple, list] = defaultdict(list)
    for a in articles:
        ext = a.stage3_extracted or {}
        key = (ext.get("client_name"), ext.get("project_name"))
        if key == (None, None):
            key = (a.id,)  # 추출 실패 시 ID로 fallback
        seen[key].append(a)

    deduped = []
    for items in seen.values():
        primary = items[0]
        primary.stage3_extracted = primary.stage3_extracted or {}
        primary.stage3_extracted["sources"] = [
            {"source": x.source, "url": x.url, "title": x.title} for x in items
        ]
        deduped.append(primary)
    return deduped


def apply_business_filters(articles: list) -> list:
    """영업팀 비즈니스 필터 (금액 450억~1조, 지역)."""
    cfg = industries()["business_filters"]
    min_amount = cfg["min_amount_billion_krw"]
    # 상한(max_amount_trillion_krw) 은 null 이면 무제한 (2026-06-04 영업팀 요청으로 제거).
    _max_cfg = cfg.get("max_amount_trillion_krw")
    max_amount = _max_cfg * 10000 if _max_cfg else None  # 1조 = 10000억, None=상한 없음
    regions = cfg["regions_include"]

    filtered = []
    for a in articles:
        ext = a.stage3_extracted or {}
        amount = ext.get("amount_billion_krw")
        if amount is not None and (
            amount < min_amount or (max_amount is not None and amount >= max_amount)
        ):
            continue
        # 지역 필터는 location 문자열에 키워드 포함 여부로 (느슨하게)
        loc = ext.get("location") or ""
        if loc and not any(r in loc for r in regions) and not any(
            kw in loc for kw in ["한국", "서울", "경기", "인천", "충청", "전라", "경상", "강원", "제주", "부산", "대구", "광주", "울산", "대전", "세종"]
        ):
            continue
        filtered.append(a)
    return filtered


def to_markdown(articles: list) -> str:
    """산업군별로 그룹핑된 Markdown 보고서."""
    by_industry: dict[str, list] = defaultdict(list)
    for a in articles:
        ind = a.stage2_industry or "UNKNOWN"
        by_industry[ind].append(a)

    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"# 주간 수주 레이더 ({today})\n",
             f"총 **{len(articles)}건** 발견.\n"]

    industries_cfg = {i["code"]: i["name"] for i in industries()["industries"]}

    for code, items in sorted(by_industry.items()):
        name = industries_cfg.get(code, code)
        lines.append(f"\n## {name} ({len(items)}건)\n")
        for a in items:
            ext = a.stage3_extracted or {}
            lines.append(f"### {ext.get('project_name') or a.title}")
            if ext.get("client_name"):
                lines.append(f"- **발주처**: {ext['client_name']}")
            if ext.get("amount_billion_krw"):
                lines.append(f"- **규모**: {ext['amount_billion_krw']:,}억원")
            if ext.get("location"):
                lines.append(f"- **위치**: {ext['location']}")
            if ext.get("investment_type"):
                lines.append(f"- **유형**: {ext['investment_type']}")
            if ext.get("schedule"):
                lines.append(f"- **일정**: {ext['schedule']}")
            if ext.get("cm_company") or ext.get("designer"):
                lines.append(f"- **CM/설계**: {ext.get('cm_company','-')} / {ext.get('designer','-')}")
            if ext.get("summary"):
                lines.append(f"- 요약: {ext['summary']}")
            sources = ext.get("sources") or [{"source": a.source, "url": a.url}]
            src_links = ", ".join(f"[{s['source']}]({s['url']})" for s in sources)
            lines.append(f"- 출처: {src_links}")
            lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="data/reports/weekly.md")
    parser.add_argument("--skip-business-filter", action="store_true")
    args = parser.parse_args()

    articles = list(read_jsonl(args.input))
    logger.info("입력 %d건", len(articles))

    articles = deduplicate(articles)
    logger.info("중복 제거 후 %d건", len(articles))

    if not args.skip_business_filter:
        articles = apply_business_filters(articles)
        logger.info("비즈니스 필터 후 %d건", len(articles))

    md = to_markdown(articles)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    logger.info("보고서 작성 완료: %s (%d bytes)", out, out.stat().st_size)


if __name__ == "__main__":
    main()
