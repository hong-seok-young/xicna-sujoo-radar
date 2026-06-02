"""일일 수집기 — RSS 수집(겹침) + Stage1 필터 → 날짜별 파일 + 풀 적재.

매일 1회 실행해 RSS 피드 롤오프(피드가 최신 N건만 보관)로 인한 누락을 방지한다.
금요일 weekly 리포트(run_weekly.py --merge-pool)가 data/pool/rss_*.jsonl 7일치를
병합해 사용한다.

DART·나라장터·식약처는 날짜범위 API라 롤오프가 없으므로 여기서 수집하지 않는다
(금요일에 --days 7 로 일괄 수집).

기본 --days 2 (하루 겹침) — 일일 실행이 한 번 건너뛰어도 구멍이 안 나게.
중복은 풀 병합 시 id 로 제거되므로 겹쳐도 안전.

사용 예:
    python scripts/run_collect.py                       # 기본 (--days 2)
    python scripts/run_collect.py --days 3 --keep-days 14
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# scripts/ 도 path 에 (run_weekly 재사용)
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_weekly import _setup_logger, run_step  # noqa: E402
from src.common.weekly_merge import add_to_pool, prune_old  # noqa: E402

KST = timezone(timedelta(hours=9))
PROJECT_ROOT = _ROOT


def main() -> int:
    ap = argparse.ArgumentParser(description="일일 RSS 수집기 (풀 적재)")
    ap.add_argument("--days", type=int, default=2, help="RSS 수집 기간(일). 기본 2 (하루 겹침)")
    ap.add_argument("--tier", type=int, default=1, choices=[1, 2, 3],
                    help="RSS 매체 tier (1=주요)")
    ap.add_argument("--keep-days", type=int, default=10,
                    help="풀에 보관할 일수. 그 밖의 오래된 파일 삭제 (기본 10)")
    ap.add_argument("--pool-pattern", default="data/pool/rss_{date}.jsonl",
                    help="풀 파일 경로 패턴 ('{date}' 포함)")
    args = ap.parse_args()

    today = datetime.now(KST).strftime("%Y-%m-%d")
    log_path = PROJECT_ROOT / "data" / "logs" / f"collect_{today}.log"
    logger = _setup_logger(log_path)
    py = sys.executable

    logger.info("=" * 60)
    logger.info(f"일일 RSS 수집 시작 ({today} KST) · --days {args.days}")
    logger.info("=" * 60)

    rss_raw = f"data/raw/{today}.jsonl"
    rss_filtered = f"data/filtered/{today}.jsonl"

    (PROJECT_ROOT / "data" / "raw").mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "data" / "filtered").mkdir(parents=True, exist_ok=True)

    steps = [
        ("[1/2] RSS 매체 수집",
         [py, "-m", "src.stage0_collect.run", "--tier", str(args.tier),
          "--days", str(args.days)]),
        ("[2/2] RSS 키워드 필터",
         [py, "-m", "src.stage1_filter.run", "--input", rss_raw]),
    ]

    fail = 0
    for name, cmd in steps:
        ok, _ = run_step(name, cmd, logger, PROJECT_ROOT)
        if not ok:
            fail += 1

    # ── 풀 적재 + 오래된 파일 정리 (수집 성공 시) ──
    if fail == 0 and (PROJECT_ROOT / rss_filtered).exists():
        n, dst = add_to_pool(PROJECT_ROOT / rss_filtered, args.pool_pattern, today)
        logger.info("─" * 60)
        logger.info(f"▶ 풀 적재: {dst} ({n}건)")
        removed = prune_old(args.pool_pattern, today, args.keep_days)
        if removed:
            logger.info(f"  오래된 풀 파일 {len(removed)}개 삭제 (>{args.keep_days}일): "
                        f"{[Path(r).name for r in removed]}")
        # 현재 풀 상태
        import glob
        pool_files = sorted(glob.glob(args.pool_pattern.replace("{date}", "*")))
        logger.info(f"  현재 풀: {len(pool_files)}일치 보관 "
                    f"{[Path(p).name for p in pool_files]}")
    else:
        logger.error("필터 결과가 없어 풀 적재 생략 (수집 단계 실패 가능)")

    logger.info("=" * 60)
    logger.info(f"일일 수집 완료 — 실패 {fail}건")
    logger.info("=" * 60)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
