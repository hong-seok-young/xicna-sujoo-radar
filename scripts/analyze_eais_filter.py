"""EAIS 필터링 시뮬레이션 — 캐시에 있는 raw items 으로 임계값별 통과율 계산.

목적: 450억 / 300억 / 200억 / 연면적 임계값별로 몇 건 → 몇 건 줄어드는지.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.stage0_collect._eais_cost import (
    COST_BY_CATEGORY,
    estimate_cost_man,
    infer_category,
)
from src.stage0_collect.eais import (
    MAX_AREA_M2,
    TARGET_PURPOSE_KEYWORDS,
    _is_new_or_extension,
    _is_target_purpose,
)

CACHE_DIR = Path("data/cache/eais")
INDUSTRIAL_DONG_CSV = Path("config/industrial_dongs.csv")


def load_dong_categories() -> dict[str, list[str]]:
    """sigungu_cd-bjdong_cd → categories list."""
    if not INDUSTRIAL_DONG_CSV.exists():
        return {}
    out: dict[str, list[str]] = {}
    with INDUSTRIAL_DONG_CSV.open("r", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            key = f"{r['sigungu_cd']}-{r['bjdong_cd']}"
            out[key] = [c for c in (r.get("categories") or "").split("|") if c]
    return out


def load_all_items() -> list[tuple[str, dict, list[str]]]:
    """(location, item, dong_categories) 튜플 리스트."""
    dong_cats = load_dong_categories()
    items: list[tuple[str, dict, list[str]]] = []
    for p in sorted(CACHE_DIR.glob("*.json")):
        if p.name == "_index.json":
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  로드 실패 {p}: {e}")
            continue
        loc = d.get("full_nm", p.stem)
        key = f"{d.get('sigungu_cd', '')}-{d.get('bjdong_cd', '')}"
        cats = dong_cats.get(key, [])
        for it in d.get("items", []):
            items.append((loc, it, cats))
    return items


def fmt_pct(n: int, total: int) -> str:
    if total == 0:
        return "0%"
    return f"{100*n/total:.1f}%"


def fmt_eok(man: int) -> str:
    if man <= 0:
        return "?"
    if man >= 10000:
        return f"{man/10000:.0f}억"
    return f"{man:,}만"


def section(title: str):
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def main():
    items = load_all_items()
    print(f"총 raw 인허가 데이터: {len(items)}건 (캐시 {sum(1 for _ in CACHE_DIR.glob('*.json'))-1}개 동)")

    # 동별 분포
    by_dong = Counter(loc for loc, _, _ in items)
    print("\n동별 분포:")
    for loc, n in by_dong.most_common():
        print(f"  {loc}: {n}건")

    section("1. 산업타겟 용도 필터 + 신축/증축 + outlier 컷")
    pre_industrial = [(loc, it, cats) for loc, it, cats in items
                      if _is_target_purpose(it.get("mainPurpsCdNm", "") or "")]
    after_gb = [(loc, it, cats) for loc, it, cats in pre_industrial
                if _is_new_or_extension(it.get("archGbCdNm", "") or "")]
    industrial = []
    n_outlier = 0
    for loc, it, cats in after_gb:
        try:
            v = float(str(it.get("totArea") or "0").replace(",", ""))
        except ValueError:
            v = 0.0
        if v > MAX_AREA_M2:
            n_outlier += 1
            continue
        industrial.append((loc, it, cats))
    print(f"용도 통과: {len(pre_industrial)}/{len(items)} ({fmt_pct(len(pre_industrial), len(items))})")
    print(f"  → 신축/증축만: {len(after_gb)}  (대수선·용도변경·이전 {len(pre_industrial)-len(after_gb)}건 컷)")
    print(f"  → outlier (>{MAX_AREA_M2:,}㎡) 컷: {n_outlier}건")
    print(f"  → 최종 산업타겟: {len(industrial)}")

    by_purpose = Counter()
    for _, it, _ in industrial:
        by_purpose[(it.get("mainPurpsCdNm", "") or "").strip()] += 1
    print("\n  통과 용도 분포 (Top 10):")
    for purp, n in by_purpose.most_common(10):
        print(f"    {purp:30s}  {n:4d}건")

    section("2. 카테고리별 분류 (CR/이차전지/제약/R&D/식품/...) — 동 클러스터 시그널 反映")
    by_category = Counter()
    for _, it, cats in industrial:
        cat = infer_category(
            it.get("mainPurpsCdNm", "") or "",
            it.get("bldNm", "") or "",
            it.get("platPlc", "") or "",
            dong_categories=cats or None,
        )
        by_category[cat] += 1
    for cat, n in by_category.most_common():
        unit = COST_BY_CATEGORY.get(cat, 100)
        print(f"  [{cat:10s}] {n:4d}건 (단가: {unit:>4}만원/㎡)")

    section("3. 추정공사비 분포")
    costs: list[tuple[int, str, dict]] = []
    no_area = 0
    for loc, it, cats in industrial:
        cost_man, cat, area = estimate_cost_man(
            it.get("mainPurpsCdNm", "") or "",
            it.get("totArea"),
            it.get("bldNm", "") or "",
            it.get("platPlc", "") or "",
            dong_categories=cats or None,
        )
        if area is None:
            no_area += 1
            continue
        costs.append((cost_man, cat, it))

    # dedup — 같은 (platPlc + bldNm) 중 최신 archPmsDay 만
    by_key: dict[str, tuple[int, str, dict]] = {}
    for loc, it, cats in industrial:
        cost_man, cat, area = estimate_cost_man(
            it.get("mainPurpsCdNm", "") or "",
            it.get("totArea"),
            it.get("bldNm", "") or "",
            it.get("platPlc", "") or "",
            dong_categories=cats or None,
        )
        if area is None:
            continue
        plat = (it.get("platPlc") or "").strip()
        bld = (it.get("bldNm") or "").strip()
        key = f"{plat}||{bld}" if (plat and bld) else f"_uniq_{it.get('mgmPmsrgstPk','')}"
        d = it.get("archPmsDay", "") or ""
        prev = by_key.get(key)
        if prev is None or d > prev[2].get("archPmsDay", ""):
            by_key[key] = (cost_man, cat, it)
    dedup_costs = [(c, cat, it) for c, cat, it in by_key.values()]
    dedup_dropped = len(costs) - len(dedup_costs)
    costs = dedup_costs
    print(f"연면적 0/누락: {no_area}건 (자동 탈락)")
    print(f"dedup 중복 컷: {dedup_dropped}건 (같은 platPlc+bldNm 변경이력)")
    print(f"공사비 추정 가능: {len(costs)}건")

    if costs:
        sorted_costs = sorted(costs, key=lambda x: -x[0])
        print("\n  최상위 10건:")
        for cost_man, cat, it in sorted_costs[:10]:
            purp = (it.get("mainPurpsCdNm") or "")[:14]
            area = it.get("totArea", "?")
            loc = (it.get("platPlc") or "")[:30]
            print(f"    {fmt_eok(cost_man):>10}  [{cat:8s}] {purp:14s}  {area}㎡  {loc}")

    section("4. 임계값별 통과 시뮬레이션")
    thresholds_eok = [50, 100, 150, 200, 300, 450, 600, 1000]
    print(f"{'임계값':>10}  {'산업타겟':>10}  {'공사비추정':>10}  {'통과':>8}  통과율")
    n_industrial = len(industrial)
    for eok in thresholds_eok:
        threshold_man = eok * 10000
        passed = sum(1 for c, _, _ in costs if c >= threshold_man)
        print(f"  {eok:>4}억     "
              f"  {n_industrial:>8}    "
              f"  {len(costs):>8}    "
              f"  {passed:>6}   {fmt_pct(passed, len(items))}")

    section("5. 연면적 기준 시뮬레이션 (참고)")
    area_thresholds = [1000, 3000, 5000, 10000, 20000, 30000, 50000]
    print(f"{'연면적':>10}  {'통과':>8}  통과율")
    for at in area_thresholds:
        passed = 0
        for _, it, _ in industrial:
            area_v = it.get("totArea")
            try:
                v = float(str(area_v or "0").replace(",", ""))
            except ValueError:
                v = 0
            if v >= at:
                passed += 1
        print(f"  {at:>5}㎡+    {passed:>6}   {fmt_pct(passed, len(items))}")

    section("6. 카테고리별 임계값 통과 (450억)")
    by_cat_passed = defaultdict(int)
    by_cat_total = defaultdict(int)
    for cost_man, cat, _ in costs:
        by_cat_total[cat] += 1
        if cost_man >= 450 * 10000:
            by_cat_passed[cat] += 1
    for cat in sorted(by_cat_total, key=lambda c: -by_cat_total[c]):
        t = by_cat_total[cat]
        p = by_cat_passed[cat]
        print(f"  [{cat:10s}]  {p}/{t}  ({fmt_pct(p, t)})")

    section("요약")
    n_total = len(items)
    n_ind = n_industrial
    n_costable = len(costs)
    n_450 = sum(1 for c, _, _ in costs if c >= 450 * 10000)
    n_300 = sum(1 for c, _, _ in costs if c >= 300 * 10000)
    n_200 = sum(1 for c, _, _ in costs if c >= 200 * 10000)
    print(f"  전체 raw:       {n_total}")
    print(f"   → 산업타겟:   {n_ind}  ({fmt_pct(n_ind, n_total)})")
    print(f"   → 공사비추정 가능: {n_costable}")
    print(f"   → 200억+:    {n_200}  ({fmt_pct(n_200, n_total)})")
    print(f"   → 300억+:    {n_300}  ({fmt_pct(n_300, n_total)})")
    print(f"   → 450억+:    {n_450}  ({fmt_pct(n_450, n_total)})")


if __name__ == "__main__":
    main()
