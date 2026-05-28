"""data/cache/test_3months/rss_raw_merged.jsonl 에 운영 daily 누적본 머지.

RSS 피드가 며칠 후 옛 기사를 떨어뜨려서 재수집 시 5/22 같은 옛 기사가
사라지는 문제 해결 — 운영 daily 가 그때 받아둔 data/raw/YYYY-MM-DD.jsonl
(prefix 없는 RSS 파일) 들을 머지에 포함.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DAYS = 90

cutoff = datetime.now(KST) - timedelta(days=DAYS)
cache = PROJECT_ROOT / "data" / "cache" / "test_3months"
raw_paths: list[Path] = []
for fn in ("rss_tier1_raw.jsonl", "rss_tier2_raw.jsonl", "rss_tier3_raw.jsonl"):
    raw_paths.append(cache / fn)

raw_dir = PROJECT_ROOT / "data" / "raw"
daily_added = 0
if raw_dir.exists():
    for f in sorted(raw_dir.glob("*.jsonl")):
        stem = f.stem
        if len(stem) != 10 or stem[4] != "-" or stem[7] != "-":
            continue
        try:
            file_dt = datetime.strptime(stem, "%Y-%m-%d").replace(tzinfo=KST)
        except ValueError:
            continue
        if file_dt < cutoff:
            continue
        raw_paths.append(f)
        daily_added += 1
        print(f"  + {f.relative_to(PROJECT_ROOT)}")
print(f"운영 daily RSS 누적본 {daily_added}개 파일 추가")

out = cache / "rss_raw_merged.jsonl"
seen: set = set()
n = 0
with out.open("w", encoding="utf-8") as fout:
    for p in raw_paths:
        if not p.exists():
            continue
        for line in p.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            k = obj.get("id") or obj.get("url")
            if k in seen:
                continue
            seen.add(k)
            fout.write(line + "\n")
            n += 1
print(f"머지 총 {n}건 → {out.relative_to(PROJECT_ROOT)}")
