"""EAIS 캐시 內 의료시설 케이스 모두 추출 → _is_hospital() 룰 검증.

확인 포인트:
  - 종합병원·의료원 키워드 매칭 케이스
  - CDMO/GMP override 작동 케이스
  - 의료시설인데 bldNm 비어있어 분류 모호한 케이스
  - 추정공사비 분포 (컷된 케이스 vs 통과 케이스)
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.stage0_collect._eais_cost import (
    _is_hospital,
    estimate_cost_man,
    HOSPITAL_KEYWORDS,
    PHARMA_PROD_OVERRIDE_KEYWORDS,
)

CACHE_DIR = Path("data/cache/eais")


def main():
    medical_items = []
    for p in sorted(CACHE_DIR.glob("*.json")):
        if p.name == "_index.json":
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for it in d.get("items", []):
            mp = it.get("mainPurpsCdNm", "") or ""
            if "의료시설" in mp:
                medical_items.append((d.get("full_nm", p.stem), it))

    print(f"=== EAIS 캐시 內 의료시설 케이스: {len(medical_items)}건 ===")
    print()

    hospital_cut = []   # _is_hospital 로 컷된 케이스
    pharma_keep = []    # CDMO override 로 유지된 케이스
    ambiguous = []      # bldNm 빈값이거나 모호
    other = []

    for loc, it in medical_items:
        bld = (it.get("bldNm") or "").strip()
        mp = it.get("mainPurpsCdNm", "") or ""
        addr = (it.get("platPlc") or "").strip()

        cost_man, cat, area = estimate_cost_man(mp, it.get("totArea"), bld, addr)
        is_hosp = _is_hospital(mp, bld)
        has_pharma = any(kw in bld for kw in PHARMA_PROD_OVERRIDE_KEYWORDS)

        rec = {"loc": loc, "bld": bld, "mp": mp, "addr": addr[:40],
               "cat": cat, "cost_eok": cost_man // 10000, "area": area, "is_hosp": is_hosp, "has_pharma": has_pharma}

        if is_hosp:
            hospital_cut.append(rec)
        elif has_pharma:
            pharma_keep.append(rec)
        elif not bld.strip():
            ambiguous.append(rec)
        else:
            other.append(rec)

    print(f"분류:")
    print(f"  종합병원 컷 ({len(hospital_cut)})    → [기타] 카테고리, 임계값 미달")
    print(f"  CDMO/GMP override ({len(pharma_keep)}) → 제약/바이오 유지")
    print(f"  bldNm 빈값/모호 ({len(ambiguous)})")
    print(f"  그 외 의료시설 ({len(other)})")
    print()

    if hospital_cut:
        print("=== 종합병원 컷 케이스 (Top 15, 면적 큰 순) ===")
        hospital_cut.sort(key=lambda r: -(r["area"] or 0))
        for r in hospital_cut[:15]:
            print(f"  [{r['cat']:6s}] {r['cost_eok']:>6}억  {r['area'] or '?':>10}㎡  «{r['bld'][:30]:30s}»  {r['addr']}")
        print()

    if pharma_keep:
        print("=== CDMO/GMP override 유지 케이스 ===")
        for r in pharma_keep[:15]:
            print(f"  [{r['cat']:8s}] {r['cost_eok']:>6}억  {r['area'] or '?':>10}㎡  «{r['bld'][:30]:30s}»")
        print()

    if ambiguous:
        print(f"=== bldNm 빈값 (Top 15, 추정공사비 큰 순) — 의료시설인데 빌딩명 없음 ===")
        ambiguous.sort(key=lambda r: -r["cost_eok"])
        for r in ambiguous[:15]:
            print(f"  [{r['cat']:8s}] {r['cost_eok']:>6}억  {r['area'] or '?':>10}㎡  loc={r['loc']:25s}  addr={r['addr']}")
        print()

    if other:
        print(f"=== 그 외 의료시설 (Top 15) — 추가 false positive 후보 검사 ===")
        other.sort(key=lambda r: -r["cost_eok"])
        for r in other[:15]:
            print(f"  [{r['cat']:8s}] {r['cost_eok']:>6}억  {r['area'] or '?':>10}㎡  «{r['bld'][:35]:35s}»  {r['addr']}")
        print()

    # 카테고리 분포 (의료시설 전체)
    print("=== 의료시설 전체 카테고리 분포 ===")
    cat_dist = Counter(r["cat"] for r in (hospital_cut + pharma_keep + ambiguous + other))
    for c, n in cat_dist.most_common():
        print(f"  {c:10s}  {n}건")


if __name__ == "__main__":
    main()
