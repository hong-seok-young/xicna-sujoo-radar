"""자이씨앤에이 수주레이더 — 주간 원클릭 실행기.

8개 단계를 순차 실행:
  [1/8] RSS 99개 매체 수집
  [2/8] RSS 키워드 필터 적용
  [3/8] 나라장터 G2B 입찰공고 수집
  [4/8] DART 시설투자 공시 수집
  [5/8] EAIS 건축인허가 수집 (전국 산업 후보 동 ~1,800개, 7일 윈도우, 450억+ 컷)
  [6/8] MFDS 의약품 GMP 적합판정 스냅샷 (diff 신규만)
  [7/8] MFDS → industrial_dongs.csv 자동 보강 (idempotent, 신규 동만 추가)
  [8/8] HTML 통합 리포트 생성 + 자동 오픈

특징:
  - 개별 단계 실패해도 나머지 단계는 계속 진행 (예: DART API 끊겨도 G2B/RSS 는 처리)
  - 각 단계의 stdout/stderr 는 콘솔에 실시간 출력
  - 단계 시작·종료·소요시간·성공여부는 data/logs/run_{date}.log 에 기록
  - 마지막에 HTML 파일 자동 오픈 (Windows: os.startfile)
  - 실행 요약을 마지막에 출력

사용법:
    python scripts/run_weekly.py
    python scripts/run_weekly.py --period-days 14   # 2주치 (DART 와 맞춤)
    python scripts/run_weekly.py --no-open          # HTML 자동 오픈 안 함
    python scripts/run_weekly.py --skip rss g2b     # 일부 단계 스킵 (디버깅용)
    python scripts/run_weekly.py --skip eais mfds   # 외부 API 끊긴 환경에서
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))

# 프로젝트 루트 = scripts 폴더의 부모
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _setup_logger(log_path: Path) -> logging.Logger:
    """콘솔 + 파일 동시 출력 로거."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("run_weekly")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


def run_step(name: str, cmd: list[str], logger: logging.Logger,
             cwd: Path) -> tuple[bool, float]:
    """단계 1개 실행. 콘솔에는 자식 출력 그대로 흘림. (성공여부, 소요초) 반환."""
    logger.info("─" * 60)
    logger.info(f"▶ {name}")
    logger.info(f"  $ {' '.join(cmd)}")
    t0 = time.time()
    try:
        # capture_output=False → 자식 프로세스의 stdout/stderr 가 부모 콘솔로 그대로 흐름.
        # 진행 상황을 실시간으로 볼 수 있음. 자식이 한글 출력해도 깨지지 않음.
        result = subprocess.run(cmd, cwd=str(cwd), check=False)
        dt = time.time() - t0
        if result.returncode == 0:
            logger.info(f"  ✓ 성공 ({dt:.1f}초)")
            return True, dt
        else:
            logger.error(f"  ✗ 실패 — returncode={result.returncode} ({dt:.1f}초)")
            return False, dt
    except FileNotFoundError as e:
        dt = time.time() - t0
        logger.error(f"  ✗ 실행 불가 — 명령을 찾을 수 없음: {e}")
        return False, dt
    except Exception as e:
        dt = time.time() - t0
        logger.error(f"  ✗ 예외 발생: {type(e).__name__}: {e}")
        return False, dt


def _open_in_browser(path: Path, logger: logging.Logger) -> None:
    """OS별로 HTML 파일 기본 브라우저로 오픈."""
    try:
        if os.name == "nt":
            os.startfile(str(path.absolute()))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
        logger.info(f"  → 브라우저 오픈: {path.absolute()}")
    except Exception as e:
        logger.warning(f"  → 브라우저 자동 오픈 실패: {e}")
        logger.warning(f"  → 수동 오픈: {path.absolute()}")


def main():
    ap = argparse.ArgumentParser(description="자이씨앤에이 수주레이더 주간 원클릭 실행기")
    ap.add_argument("--period-days", type=int, default=7,
                    help="리포트 표시 기간 (일). 기본 7. DART 수집은 항상 14일.")
    ap.add_argument("--rss-days", type=int, default=7, help="RSS 수집 기간 (일)")
    ap.add_argument("--g2b-days", type=int, default=7, help="G2B 수집 기간 (일)")
    ap.add_argument("--dart-days", type=int, default=14, help="DART 수집 기간 (일)")
    ap.add_argument("--eais-days", type=int, default=7,
                    help="EAIS 인허가 룩백 윈도우 (일). 기본 7 (주간 사이클). "
                         "참고: 450억+급 인허가는 전국 연 ~27건으로 희소 → 7일이면 거의 항상 0건. "
                         "리드를 더 보려면 30~90 으로 늘릴 것.")
    ap.add_argument("--eais-threshold-eok", type=float, default=300,
                    help="EAIS 추정공사비 임계값 (억). 기본 300. (CLAUDE.md 영업 하한은 450억이나 "
                         "EAIS 추정단가가 보수적이라 300으로 낮춰 후보 확보.)")
    ap.add_argument("--eais-quota", type=int, default=900,
                    help="EAIS API 호출 quota (1,000건/일 中 마진 100)")
    ap.add_argument("--tier", type=int, default=1, choices=[1, 2, 3],
                    help="RSS 매체 tier (1=주요 99개)")
    ap.add_argument("--no-open", action="store_true", help="HTML 자동 오픈 비활성화")
    ap.add_argument("--insecure", action="store_true",
                    help="SSL 검증 비활성화 (G2B/DART/EAIS/MFDS 회사망에서 SSL 문제 시)")
    ap.add_argument("--skip", nargs="+", default=[],
                    choices=["rss", "filter", "g2b", "dart", "eais", "mfds", "enrich", "report"],
                    help="스킵할 단계 (디버깅용)")
    args = ap.parse_args()

    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    log_path = PROJECT_ROOT / "data" / "logs" / f"run_{today_str}.log"
    logger = _setup_logger(log_path)

    logger.info("=" * 60)
    logger.info(f"자이씨앤에이 수주레이더 — 주간 실행 시작 ({today_str} KST)")
    logger.info(f"로그: {log_path}")
    logger.info("=" * 60)

    # 파일 경로 (각 단계 기본 출력 경로와 동일)
    rss_raw = f"data/raw/{today_str}.jsonl"
    rss_filtered = f"data/filtered/{today_str}.jsonl"
    g2b_raw = f"data/raw/g2b_{today_str}.jsonl"
    dart_raw = f"data/raw/dart_{today_str}.jsonl"
    eais_raw = f"data/raw/eais_{today_str}.jsonl"
    mfds_raw = f"data/raw/mfds_gmp_{today_str}.jsonl"

    py = sys.executable  # 현재 파이썬 인터프리터 (venv 환경 유지)

    insecure_flag = ["--insecure"] if args.insecure else []

    # 단계 정의: (id, 표시명, cmd) — id 는 --skip 에 사용
    all_steps = [
        ("rss",
         "[1/8] RSS 99개 매체 수집",
         [py, "-m", "src.stage0_collect.run",
          "--tier", str(args.tier), "--days", str(args.rss_days)]),
        ("filter",
         "[2/8] RSS 키워드 필터 적용",
         [py, "-m", "src.stage1_filter.run", "--input", rss_raw]),
        ("g2b",
         "[3/8] 나라장터 G2B 입찰공고 수집",
         [py, "-m", "src.stage0_collect.nara", "--days", str(args.g2b_days)] + insecure_flag),
        ("dart",
         "[4/8] DART 시설투자 공시 수집",
         [py, "-m", "src.stage0_collect.dart", "--days", str(args.dart_days)] + insecure_flag),
        ("eais",
         f"[5/8] EAIS 건축인허가 수집 ({args.eais_threshold_eok:.0f}억+, 산업 후보 동 화이트리스트)",
         [py, "-m", "src.stage0_collect.eais",
          "--days", str(args.eais_days),
          "--threshold-eok", str(args.eais_threshold_eok),
          "--quota", str(args.eais_quota)] + insecure_flag),
        ("mfds",
         "[6/8] MFDS 의약품 GMP 적합판정 (diff 신규)",
         [py, "-m", "src.stage0_collect.mfds_gmp"] + insecure_flag),
        ("enrich",
         "[7/8] MFDS → industrial_dongs.csv 자동 보강 (idempotent)",
         [py, "scripts/enrich_dongs_from_mfds.py", "--apply"]),
        ("report",
         "[8/8] HTML 통합 리포트 생성",
         [py, "scripts/daily_report_html.py",
          "--rss", rss_filtered,
          "--g2b", g2b_raw,
          "--dart", dart_raw,
          "--eais", eais_raw,
          "--mfds", mfds_raw,
          "--period-days", str(args.period_days),
          "--eais-days", str(args.eais_days),
          "--eais-threshold-eok", str(args.eais_threshold_eok)]),
    ]

    results: list[tuple[str, bool, float, bool]] = []  # (name, ok, dt, skipped)
    overall_t0 = time.time()
    for step_id, name, cmd in all_steps:
        if step_id in args.skip:
            logger.info("─" * 60)
            logger.info(f"⊘ {name} — 스킵됨 (--skip {step_id})")
            results.append((name, True, 0.0, True))
            continue
        ok, dt = run_step(name, cmd, logger, PROJECT_ROOT)
        results.append((name, ok, dt, False))

    overall_dt = time.time() - overall_t0

    # === 요약 ===
    logger.info("=" * 60)
    logger.info("실행 요약")
    logger.info("=" * 60)
    success_count = 0
    fail_count = 0
    skip_count = 0
    for name, ok, dt, skipped in results:
        if skipped:
            mark = "⊘ 스킵"
            skip_count += 1
        elif ok:
            mark = f"✓ 성공 ({dt:5.1f}초)"
            success_count += 1
        else:
            mark = f"✗ 실패 ({dt:5.1f}초)"
            fail_count += 1
        logger.info(f"  {mark}  {name}")
    logger.info("─" * 60)
    logger.info(f"총 {len(results)}단계 / 성공 {success_count} / 실패 {fail_count} / "
                f"스킵 {skip_count} / 전체 {overall_dt:.1f}초")
    logger.info(f"로그 파일: {log_path}")

    # === HTML 자동 오픈 ===
    html_path = PROJECT_ROOT / "data" / f"daily_report_{today_str}.html"
    if html_path.exists():
        logger.info(f"HTML 리포트: {html_path}")
        if not args.no_open:
            _open_in_browser(html_path, logger)
        else:
            logger.info("  (--no-open 으로 자동 오픈 생략)")
    else:
        logger.error(f"HTML 리포트 파일 없음: {html_path}")
        logger.error("  → 5단계 (report) 가 실패했거나 스킵된 것으로 보임.")

    logger.info("=" * 60)
    logger.info("주간 실행 완료")
    logger.info("=" * 60)

    # 1개 이상 실패 시 non-zero exit (작업 스케줄러에서 결과 인지 가능)
    sys.exit(0 if fail_count == 0 else 1)


if __name__ == "__main__":
    main()
