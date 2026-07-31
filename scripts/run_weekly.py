"""자이씨앤에이 수주레이더 — 주간 원클릭 실행기.

7개 단계를 순차 실행 (세움터/EAIS는 영업팀 요청으로 제거됨 2026-06-02):
  [1/7] RSS 99개 매체 수집
  [2/7] RSS 키워드 필터 적용
  [3/7] 나라장터 G2B 입찰공고 수집
  [4/7] DART 시설투자 공시 수집
  [5/7] MFDS 의약품 GMP 적합판정 스냅샷 (diff 신규만)
  [6/7] MFDS → industrial_dongs.csv 자동 보강 (idempotent, 신규 동만 추가)
  [7/7] HTML 통합 리포트 생성 + 자동 오픈

특징:
  - 개별 단계 실패해도 나머지 단계는 계속 진행 (예: DART API 끊겨도 G2B/RSS 는 처리)
  - 각 단계의 stdout/stderr 는 콘솔에 실시간 출력
  - 단계 시작·종료·소요시간·성공여부는 data/logs/run_{date}.log 에 기록
  - 마지막에 HTML 파일 자동 오픈 (Windows: os.startfile)
  - 실행 요약을 마지막에 출력

사용법:
    python scripts/run_weekly.py
    python scripts/run_weekly.py --merge-pool        # 본 운영: 매일 누적된 RSS 풀 7일 병합
    python scripts/run_weekly.py --period-days 14   # 2주치 (DART 와 맞춤)
    python scripts/run_weekly.py --no-open          # HTML 자동 오픈 안 함
    python scripts/run_weekly.py --skip rss g2b     # 일부 단계 스킵 (디버깅용)
    python scripts/run_weekly.py --skip mfds        # 외부 API 끊긴 환경에서
    python scripts/run_weekly.py --allow-partial    # 본 운영(CI): 소스 일부 실패해도 리포트 나가면 exit 0

수집 구조:
  - 일일: scripts/run_collect.py (매일 RSS 수집·필터 → data/pool 누적). RSS 롤오프 방지.
  - 금요일: 본 스크립트 --merge-pool (풀 7일 병합 + DART/G2B/MFDS 일괄 + 리포트).
    DART·나라장터·식약처는 날짜범위 API라 롤오프가 없어 금요일 --days 7 일괄로 충분.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))

# 프로젝트 루트 = scripts 폴더의 부모
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _setup_logger(log_path: Path) -> logging.Logger:
    """콘솔 + 파일 동시 출력 로거."""
    # 자식 출력을 부모 콘솔로 다시 흘리므로(run_step), cp949 콘솔에서 '✓'·한글이
    # UnicodeEncodeError 를 내지 않도록 stdout 을 UTF-8/replace 로 재설정.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass
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


TAIL_LINES = 40  # 실패한 단계의 마지막 출력 보관 줄 수 (실패 알림 메일에 실림)


def run_step(name: str, cmd: list[str], logger: logging.Logger,
             cwd: Path, tail_out: list[str] | None = None) -> tuple[bool, float]:
    """단계 1개 실행. 자식 출력을 콘솔 + 로그파일에 동시 기록. (성공여부, 소요초) 반환.

    tail_out 을 주면 실패 시 자식의 마지막 TAIL_LINES 줄을 거기에 담아 준다.
    (CI 가 run_status.json 으로 읽어 운영자 메일에 실제 traceback 을 실어 보낸다 —
     GitHub 로그를 열 수 없는 상황에서도 원인 파악이 되도록.)
    """
    logger.info("─" * 60)
    logger.info(f"▶ {name}")
    logger.info(f"  $ {' '.join(cmd)}")
    t0 = time.time()
    tail: deque[str] = deque(maxlen=TAIL_LINES)
    try:
        # 자식 출력을 파이프로 받아 한 줄씩 콘솔·로그파일에 흘린다(실시간성 유지).
        # PYTHONIOENCODING/UNBUFFERED: 자식이 UTF-8 로, 줄 단위로 바로 내보내게 강제.
        child_env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        proc = subprocess.Popen(cmd, cwd=str(cwd), env=child_env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace",
                                bufsize=1)
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            print(line, flush=True)   # 콘솔(=CI 로그) 실시간
            _log_child_line(logger, line)  # 로그파일에도 남김 (실패 진단용)
            tail.append(line)
        rc = proc.wait()
        dt = time.time() - t0
        if rc == 0:
            logger.info(f"  ✓ 성공 ({dt:.1f}초)")
            return True, dt
        logger.error(f"  ✗ 실패 — returncode={rc} ({dt:.1f}초)")
        if tail_out is not None:
            tail_out.extend(tail)
        return False, dt
    except FileNotFoundError as e:
        dt = time.time() - t0
        logger.error(f"  ✗ 실행 불가 — 명령을 찾을 수 없음: {e}")
        if tail_out is not None:
            tail_out.append(f"FileNotFoundError: {e}")
        return False, dt
    except Exception as e:
        dt = time.time() - t0
        logger.error(f"  ✗ 예외 발생: {type(e).__name__}: {e}")
        if tail_out is not None:
            tail_out.extend(list(tail) + [f"{type(e).__name__}: {e}"])
        return False, dt


def _log_child_line(logger: logging.Logger, line: str) -> None:
    """자식 출력 한 줄을 로그 '파일' 핸들러에만 기록 (콘솔 중복 출력 방지)."""
    for h in logger.handlers:
        if isinstance(h, logging.FileHandler):
            try:
                h.stream.write(f"    | {line}\n")
            except Exception:
                pass


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
                    help="리포트 표시 기간 (일). 기본 7 (주간 사이클).")
    ap.add_argument("--rss-days", type=int, default=7, help="RSS 수집 기간 (일)")
    ap.add_argument("--g2b-days", type=int, default=7, help="G2B 수집 기간 (일)")
    ap.add_argument("--dart-days", type=int, default=7, help="DART 수집 기간 (일, 주간 사이클)")
    ap.add_argument("--tier", type=int, default=1, choices=[1, 2, 3],
                    help="RSS 매체 tier (1=주요 99개)")
    ap.add_argument("--no-open", action="store_true", help="HTML 자동 오픈 비활성화")
    ap.add_argument("--insecure", action="store_true",
                    help="SSL 검증 비활성화 (G2B/DART/MFDS 회사망에서 SSL 문제 시)")
    ap.add_argument("--merge-pool", action="store_true",
                    help="매일 누적된 RSS 풀(data/pool)을 7일 병합해 리포트에 사용 "
                         "(RSS 롤오프 누락 방지). 미지정 시 오늘 하루치만 사용(레거시).")
    ap.add_argument("--pool-pattern", default="data/pool/rss_{date}.jsonl",
                    help="RSS 풀 파일 경로 패턴 ('{date}' 포함). --merge-pool 일 때만.")
    ap.add_argument("--keep-days", type=int, default=10,
                    help="풀 보관 일수 (그 밖은 정리). --merge-pool 일 때만. 기본 10.")
    ap.add_argument("--skip", nargs="+", default=[],
                    choices=["rss", "filter", "collect", "g2b", "dart", "mfds",
                             "enrich", "merge", "report"],
                    help="스킵할 단계 (디버깅용)")
    ap.add_argument("--allow-partial", action="store_true",
                    help="일부 수집 단계가 실패해도 리포트가 생성됐으면 exit 0. "
                         "본 운영(CI)용 — 외부 API 한 곳이 죽어도 주간 발송은 나가야 한다. "
                         "리포트 자체가 없으면 여전히 exit 1.")
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
    mfds_raw = f"data/raw/mfds_gmp_{today_str}.jsonl"
    week_rss = f"data/week/rss_{today_str}.jsonl"  # 풀 7일 병합 결과 (--merge-pool)
    # 리포트가 읽을 RSS: 병합 모드면 7일 취합본, 아니면 오늘 하루치 필터본(레거시)
    report_rss = week_rss if args.merge_pool else rss_filtered

    py = sys.executable  # 현재 파이썬 인터프리터 (venv 환경 유지)

    insecure_flag = ["--insecure"] if args.insecure else []

    # 단계 정의: (id, 표시명, cmd) — id 는 --skip 에 사용
    if args.merge_pool:
        # 본 운영(금요일): 매일 누적된 RSS 풀을 7일 병합해 사용 (롤오프 누락 방지).
        # RSS 수집·필터·풀 적재는 run_collect 가 한 번에 처리.
        all_steps = [
            ("collect",
             "[1/7] RSS 수집+필터+풀 적재 (run_collect)",
             [py, "scripts/run_collect.py", "--days", str(args.rss_days),
              "--tier", str(args.tier), "--keep-days", str(args.keep_days),
              "--pool-pattern", args.pool_pattern]),
            ("g2b",
             "[2/7] 나라장터 G2B 입찰공고 수집",
             [py, "-m", "src.stage0_collect.nara", "--days", str(args.g2b_days)] + insecure_flag),
            ("dart",
             "[3/7] DART 시설투자 공시 수집",
             [py, "-m", "src.stage0_collect.dart", "--days", str(args.dart_days)] + insecure_flag),
            ("mfds",
             "[4/7] MFDS 의약품 GMP 적합판정 (diff 신규)",
             [py, "-m", "src.stage0_collect.mfds_gmp"] + insecure_flag),
            ("enrich",
             "[5/7] MFDS → industrial_dongs.csv 자동 보강 (idempotent)",
             [py, "scripts/enrich_dongs_from_mfds.py", "--apply"]),
            ("merge",
             "[6/7] 주간 RSS 취합 (풀 7일 병합·중복제거)",
             [py, "scripts/merge_week.py", "--pattern", args.pool_pattern,
              "--days", str(args.period_days), "--today", today_str, "--out", week_rss]),
            ("report",
             "[7/7] HTML 통합 리포트 생성",
             [py, "scripts/daily_report_html.py",
              "--rss", report_rss,
              "--g2b", g2b_raw,
              "--dart", dart_raw,
              "--mfds", mfds_raw,
              "--period-days", str(args.period_days)]),
        ]
    else:
        # 레거시(로컬·단발): 오늘 1회 --days N 수집 → 하루치 필터본으로 리포트.
        all_steps = [
            ("rss",
             "[1/7] RSS 99개 매체 수집",
             [py, "-m", "src.stage0_collect.run",
              "--tier", str(args.tier), "--days", str(args.rss_days)]),
            ("filter",
             "[2/7] RSS 키워드 필터 적용",
             [py, "-m", "src.stage1_filter.run", "--input", rss_raw]),
            ("g2b",
             "[3/7] 나라장터 G2B 입찰공고 수집",
             [py, "-m", "src.stage0_collect.nara", "--days", str(args.g2b_days)] + insecure_flag),
            ("dart",
             "[4/7] DART 시설투자 공시 수집",
             [py, "-m", "src.stage0_collect.dart", "--days", str(args.dart_days)] + insecure_flag),
            ("mfds",
             "[5/7] MFDS 의약품 GMP 적합판정 (diff 신규)",
             [py, "-m", "src.stage0_collect.mfds_gmp"] + insecure_flag),
            ("enrich",
             "[6/7] MFDS → industrial_dongs.csv 자동 보강 (idempotent)",
             [py, "scripts/enrich_dongs_from_mfds.py", "--apply"]),
            ("report",
             "[7/7] HTML 통합 리포트 생성",
             [py, "scripts/daily_report_html.py",
              "--rss", report_rss,
              "--g2b", g2b_raw,
              "--dart", dart_raw,
              "--mfds", mfds_raw,
              "--period-days", str(args.period_days)]),
        ]

    results: list[tuple[str, bool, float, bool]] = []  # (name, ok, dt, skipped)
    fail_tails: dict[str, list[str]] = {}  # 실패 단계명 → 자식 출력 마지막 N줄
    overall_t0 = time.time()
    for step_id, name, cmd in all_steps:
        if step_id in args.skip:
            logger.info("─" * 60)
            logger.info(f"⊘ {name} — 스킵됨 (--skip {step_id})")
            results.append((name, True, 0.0, True))
            continue
        tail: list[str] = []
        ok, dt = run_step(name, cmd, logger, PROJECT_ROOT, tail_out=tail)
        if not ok and tail:
            fail_tails[name] = tail
        results.append((name, ok, dt, False))

    overall_dt = time.time() - overall_t0

    # === 요약 ===
    logger.info("=" * 60)
    logger.info("실행 요약")
    logger.info("=" * 60)
    success_count = 0
    fail_count = 0
    skip_count = 0
    failed_names: list[str] = []
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
            failed_names.append(name)
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
        logger.error("  → 마지막 단계 (report) 가 실패했거나 스킵된 것으로 보임.")

    # === 실행 상태 파일 (CI 가 읽어 '부분 실패 vs 완전 실패' 를 구분) ===
    # 워크플로가 이 파일로 실패한 단계명을 뽑아 경고 메일/스텝 요약에 넣는다.
    status_path = PROJECT_ROOT / "data" / "logs" / "run_status.json"
    try:
        status_path.write_text(json.dumps({
            "date": today_str,
            "failed_steps": failed_names,
            # 실패 단계의 자식 출력 꼬리 (traceback). 운영자 알림 메일에 실린다.
            "failed_tails": fail_tails,
            "counts": {"success": success_count, "fail": fail_count, "skip": skip_count},
            "report_exists": html_path.exists(),
            "report_path": str(html_path),
            "elapsed_sec": round(overall_dt, 1),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"실행 상태: {status_path}")
    except Exception as e:  # 상태파일 실패가 파이프라인을 막지 않게
        logger.warning(f"실행 상태 파일 기록 실패: {type(e).__name__}: {e}")

    logger.info("=" * 60)
    logger.info("주간 실행 완료")
    logger.info("=" * 60)

    # 종료코드 정책
    #  - 전부 성공: 0
    #  - 일부 실패 + 리포트 생성됨 + --allow-partial: 0
    #      외부 API 한 곳(G2B·DART·MFDS)이 죽어도 나머지 섹션으로 주간 발송은 나가야 한다.
    #      실패한 단계는 run_status.json 으로 CI 에 전달돼 운영자에게 경고 메일이 간다.
    #  - 그 밖(리포트 없음 등): 1
    if fail_count == 0:
        sys.exit(0)
    if args.allow_partial and html_path.exists():
        logger.warning(f"부분 실패 {fail_count}건({', '.join(failed_names)}) — "
                       f"리포트는 생성됨 → exit 0 (--allow-partial)")
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
