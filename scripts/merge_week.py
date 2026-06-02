"""주간 RSS 취합 CLI — 날짜별 풀을 N일 윈도우로 병합(중복제거)해 단일 파일로.

사용 예:
    python scripts/merge_week.py \
        --pattern "data/pool/rss_{date}.jsonl" --days 7 \
        --out data/week/rss_2026-06-02.jsonl

'{date}' 자리에 윈도우 각 날짜(YYYY-MM-DD)가 들어간다. 결번 파일은 건너뛴다.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 프로젝트 루트를 path 에 추가 (python scripts/merge_week.py 직접 실행 대응)
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.common.weekly_merge import merge_window, write_jsonl  # noqa: E402

KST = timezone(timedelta(hours=9))


def main() -> int:
    ap = argparse.ArgumentParser(description="주간 RSS 취합 (날짜별 풀 7일 병합·중복제거)")
    ap.add_argument("--pattern", default="data/pool/rss_{date}.jsonl",
                    help="'{date}' 포함 경로 패턴 (기본: data/pool/rss_{date}.jsonl)")
    ap.add_argument("--days", type=int, default=7, help="윈도우 일수 (기본 7)")
    ap.add_argument("--today", default=datetime.now(KST).strftime("%Y-%m-%d"),
                    help="윈도우 종료일 (기본: 오늘 KST)")
    ap.add_argument("--out", required=True, help="병합 결과 출력 경로")
    args = ap.parse_args()

    items, stats = merge_window(args.pattern, args.today, args.days)
    write_jsonl(items, args.out)

    w = stats["window"]
    print(f"[merge] 윈도우 {w[0]} ~ {w[-1]} ({args.days}일) · "
          f"파일 {stats['files_found']}개 발견 / {stats['files_missing']}개 결번")
    for d, c in stats["per_day"].items():
        print(f"    {d}: {c}건")
    print(f"[merge] 원시 {stats['raw_count']}건 → 중복 {stats['dup_removed']}건 제거 "
          f"→ {stats['merged_count']}건 → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
