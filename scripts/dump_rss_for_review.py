"""90일치 RSS filtered 결과를 사람(또는 Claude) 검토용 텍스트로 dump.

각 row 1줄 짜리 요약 + 본문 preview 300자. tier(HIGH/MID/LOW) + stage1 매칭 패턴 같이.
출력: data/cache/test_3months/rss_review_dump.txt
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from daily_report_html import classify_rss  # noqa: E402

IN = PROJECT_ROOT / "data" / "cache" / "test_3months" / "rss_filtered.jsonl"
OUT = PROJECT_ROOT / "data" / "cache" / "test_3months" / "rss_review_dump.txt"

items = []
with IN.open(encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            items.append(json.loads(line))

classified = [(classify_rss(it), it) for it in items]
high = [(r, it) for (l, r), it in classified if l == "HIGH"]
mid = [(r, it) for (l, r), it in classified if l == "MID"]
low = [(r, it) for (l, r), it in classified if l == "LOW"]

with OUT.open("w", encoding="utf-8") as f:
    for tier_label, bucket in (("HIGH", high), ("MID", mid), ("LOW", low)):
        f.write(f"\n{'='*70}\n== {tier_label}  ({len(bucket)}건)\n{'='*70}\n\n")
        for i, (reason, it) in enumerate(bucket, 1):
            source = it.get("source", "")
            cats = it.get("categories") or []
            patterns = it.get("stage1_matched_patterns", [])
            title = (it.get("title") or "").strip()
            content = (it.get("content") or "").strip().replace("\n", " ")
            content_preview = content[:300]
            pdate = (it.get("published_at") or "")[:10]
            f.write(f"[{tier_label}-{i:03d}] {pdate} | {source} | cat={','.join(cats)}\n")
            f.write(f"  T: {title}\n")
            f.write(f"  C: {content_preview}\n")
            f.write(f"  patterns: {patterns}\n")
            f.write(f"  stage1 reason: {reason}\n\n")

print(f"총 {len(items)}건 → HIGH {len(high)} / MID {len(mid)} / LOW {len(low)}")
print(f"저장: {OUT}")
