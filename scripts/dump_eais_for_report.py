"""캐시 + simulate_period.filter_pipeline 으로 N일 통과건 → JSONL 저장.

daily_report_html.py 입력 형식에 맞춤 (title/content/url/published_at).

   python scripts/dump_eais_for_report.py --days 7 --out data/raw/eais_2026-05-22.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.simulate_period import filter_pipeline, load_all_items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--out", type=str, required=True)
    ap.add_argument("--collected-at", type=str, default="2026-05-22T08:30:00+09:00")
    args = ap.parse_args()

    items = load_all_items()
    print(f"캐시 raw: {len(items)}건 (캐시 동 보유)")
    rows = filter_pipeline(items, days=args.days)
    print(f"{args.days}일 통과: {len(rows)}건")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    with out.open("w", encoding="utf-8") as f:
        for r in rows:
            it = r["item"]
            cost_man = r["cost_man"]
            eok = cost_man // 10000
            man = cost_man % 10000
            cost_str = f"{eok:,}억" if man == 0 else f"{eok:,}억 {man:,}만"

            bld = (it.get("bldNm") or "건물명미상").strip() or "건물명미상"
            purp = (it.get("mainPurpsCdNm") or "").strip()
            gb = (it.get("archGbCdNm") or "").strip()
            tot_area = it.get("totArea") or "?"
            plat = (it.get("platPlc") or "").strip()
            arch_day = r["archDay"]

            title = f"[{r['category']}] {bld[:40]} ({purp}, 추정 {cost_str})"
            content = " | ".join([
                f"위치: {plat}" if plat else "",
                f"연면적 {tot_area}㎡",
                f"인허가일 {arch_day}",
                f"종별 {gb}" if gb else "",
                f"추정공사비 {cost_str}",
            ]).strip(" |")

            published_at = (
                f"{arch_day[:4]}-{arch_day[4:6]}-{arch_day[6:8]}T00:00:00+09:00"
                if arch_day and len(arch_day) >= 8
                else "2026-05-22T00:00:00+09:00"
            )

            record = {
                "id": it.get("mgmPmsrgstPk", ""),
                "source": "eais.go.kr",
                "url": "https://cloud.eais.go.kr/",
                "title": title,
                "content": content,
                "published_at": published_at,
                "collected_at": args.collected_at,
                "category": r["category"],
                "cost_man": cost_man,
                "loc": r["loc"],
                "categories": [r["category"]],   # daily_report_html 의 _cats_for() 용
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"저장 완료: {out}  ({len(rows)}건)")


if __name__ == "__main__":
    main()
