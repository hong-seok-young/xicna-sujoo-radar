"""기간별 (7일 / 30일 / 90일 / 365일) EAIS 통과 건수 시뮬레이션.

캐시 데이터로 — 실제 collect 를 안 부르고도 운영 사이클 감각 잡기.
   python scripts/simulate_period.py

전국 전체 동 캐시가 다 있으면 풀스윕 결과. 일부만 있으면 그 부분만.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.stage0_collect._eais_cost import (
    DEFAULT_THRESHOLD_MAN,
    estimate_cost_man,
    format_cost,
    passes_threshold,
)
from src.stage0_collect.eais import (
    MAX_AREA_M2,
    _is_new_or_extension,
    _is_target_purpose,
)

KST = timezone(timedelta(hours=9))
CACHE_DIR = Path("data/cache/eais")
INDUSTRIAL_DONG_CSV = Path("config/industrial_dongs.csv")

# 비교할 기간 (일)
PERIODS = [7, 30, 90, 365]


def load_dong_categories() -> dict[str, list[str]]:
    if not INDUSTRIAL_DONG_CSV.exists():
        return {}
    out: dict[str, list[str]] = {}
    with INDUSTRIAL_DONG_CSV.open("r", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            key = f"{r['sigungu_cd']}-{r['bjdong_cd']}"
            out[key] = [c for c in (r.get("categories") or "").split("|") if c]
    return out


def load_all_items() -> list[tuple[str, dict, list[str]]]:
    """캐시 → (location, item, dong_categories) 리스트."""
    dong_cats = load_dong_categories()
    items: list[tuple[str, dict, list[str]]] = []
    for p in sorted(CACHE_DIR.glob("*.json")):
        if p.name == "_index.json":
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        loc = d.get("full_nm", p.stem)
        key = f"{d.get('sigungu_cd', '')}-{d.get('bjdong_cd', '')}"
        cats = dong_cats.get(key, [])
        for it in d.get("items", []):
            items.append((loc, it, cats))
    return items


def filter_pipeline(items: list[tuple[str, dict, list[str]]],
                    days: int,
                    threshold_man: int = DEFAULT_THRESHOLD_MAN) -> list[dict]:
    """eais.py 와 동일한 필터 체인 — 통과 건만 반환 (dict)."""
    now = datetime.now(KST)
    threshold_str = (now - timedelta(days=days)).strftime("%Y%m%d")

    candidates: list[tuple[int, str, dict, str, str]] = []  # cost, cat, item, loc, archDay
    for loc, it, cats in items:
        purpose = it.get("mainPurpsCdNm", "") or ""
        if not _is_target_purpose(purpose):
            continue
        d = it.get("archPmsDay", "") or ""
        if d and d < threshold_str:
            continue
        if not _is_new_or_extension(it.get("archGbCdNm", "") or ""):
            continue
        try:
            area_v = float(str(it.get("totArea") or "0").replace(",", ""))
        except ValueError:
            area_v = 0.0
        if area_v > MAX_AREA_M2:
            continue
        cost_man, category, _ = estimate_cost_man(
            purpose,
            it.get("totArea"),
            it.get("bldNm", "") or "",
            it.get("platPlc", "") or "",
            dong_categories=cats or None,
        )
        if not passes_threshold(cost_man, threshold_man):
            continue
        candidates.append((cost_man, category, it, loc, d))

    # dedup
    best: dict[str, tuple[int, str, dict, str, str]] = {}
    for cost, cat, it, loc, d in candidates:
        plat = (it.get("platPlc") or "").strip()
        bld = (it.get("bldNm") or "").strip()
        key = f"{plat}||{bld}" if (plat and bld) else f"_uniq_{it.get('mgmPmsrgstPk','')}"
        prev = best.get(key)
        if prev is None or d > prev[4]:
            best[key] = (cost, cat, it, loc, d)
    return [{"cost_man": c, "category": cat, "item": it, "loc": loc, "archDay": d}
            for c, cat, it, loc, d in best.values()]


def summary_block(period_results: dict[int, list[dict]]):
    """기간별 요약 표."""
    print()
    print("=" * 76)
    print(f"{'기간':>8}  {'통과건':>6}  {'CR':>4}  {'이차전지':>5}  {'제약':>4}  {'R&D':>4}  {'일반생산':>6}  {'기타':>4}  최대공사비")
    print("-" * 76)
    for days in PERIODS:
        rows = period_results[days]
        cat_cnt = Counter(r["category"] for r in rows)
        top = max((r["cost_man"] for r in rows), default=0)
        print(f"  {days:>3}일   "
              f"  {len(rows):>4}  "
              f"  {cat_cnt.get('CR',0):>2}  "
              f"  {cat_cnt.get('이차전지',0):>3}  "
              f"  {cat_cnt.get('제약/바이오',0):>2}  "
              f"  {cat_cnt.get('R&D',0):>2}  "
              f"  {cat_cnt.get('일반생산',0):>4}  "
              f"  {cat_cnt.get('기타',0):>2}  "
              f"  {format_cost(top)}")


def detail_block(rows: list[dict], days: int, top_n: int = 15):
    """기간별 통과 건 상세."""
    print()
    print("=" * 76)
    print(f"[{days}일] 통과 {len(rows)}건 — 상위 {min(top_n, len(rows))} 건")
    print("-" * 76)
    rows_sorted = sorted(rows, key=lambda r: -r["cost_man"])
    for r in rows_sorted[:top_n]:
        it = r["item"]
        purp = (it.get("mainPurpsCdNm") or "")[:10]
        bld = (it.get("bldNm") or "—")[:18]
        addr = (it.get("platPlc") or "")[:35]
        gb = (it.get("archGbCdNm") or "")[:4]
        d = r["archDay"][:8] if r["archDay"] else "-"
        print(f"  {format_cost(r['cost_man']):>10}  "
              f"[{r['category']:8s}] "
              f"{d}  {gb:4s} "
              f"{purp:10s} «{bld:18s}» {addr}")


def main():
    items = load_all_items()
    print(f"캐시 raw items: {len(items)} (캐시 동 {sum(1 for _ in CACHE_DIR.glob('*.json'))-1}개)")

    period_results: dict[int, list[dict]] = {}
    for days in PERIODS:
        period_results[days] = filter_pipeline(items, days)

    summary_block(period_results)
    for days in PERIODS:
        detail_block(period_results[days], days)


if __name__ == "__main__":
    main()
