"""3개월(기본 90일) 윈도우 테스트 리포트 — 실제 API 재수집 + 필터링 검토용.

각 소스(G2B/DART/EAIS/RSS)를 90일 윈도우로 **직접 API 호출**해서 수집한다.
이전 버전은 일별 jsonl 머지에 그쳤지만 — 데이터가 5월 며칠치밖에 없어서
'무늬만 90일'이었다. 이번엔 실제 90일치를 가져와서 필터링 규칙이 어떻게
작동하는지 검증한다.

MFDS 는 사용자 지시대로 가장 최신 파일 그대로 사용 (일별 누적 의미 적음).

호출되는 모듈 (모두 기존 코드 그대로):
  src.stage0_collect.nara   — G2B  (--days N)
  src.stage0_collect.dart   — DART (--days N)
  src.stage0_collect.eais   — EAIS (--days N, 시간 오래 걸림)
  src.stage0_collect.run    — RSS 피드 (--tier 1/2/3 --days N)
  src.stage1_filter.run     — RSS 필터링 (action/target/money/area 패턴)
  scripts.daily_report_html — 최종 리포트 생성

수집 결과는 data/cache/test_3months/*.jsonl 에 저장 (운영 일별 파일과 분리).
출력 파일: data/daily_report_TEST_{N}DAYS_{today}.html

사용:
    python scripts/test_report_3months.py                 # 전체 90일치 재수집
    python scripts/test_report_3months.py --days 30
    python scripts/test_report_3months.py --skip g2b,eais  # 일부 단계 건너뛰기 (캐시 사용)
    python scripts/test_report_3months.py --only-report    # 수집 없이 리포트만 (캐시된 데이터 사용)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KST = timezone(timedelta(hours=9))

ALL_STEPS = ["g2b", "dart", "eais", "rss"]  # mfds 는 별도


def _run_module(module: str, args: list[str], *, label: str,
                timeout: int = 1800) -> bool:
    """`python -m module ...` 실행. True/False 성공 여부."""
    cmd = [sys.executable, "-m", module] + args
    print(f"\n▶ [{label}] {' '.join(args)}")
    t0 = time.time()
    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"  ⏱  [{label}] 타임아웃 ({timeout}초) — 부분 결과만 저장됐을 수 있음")
        return False
    dt = time.time() - t0
    if result.returncode != 0:
        print(f"  ❌ [{label}] 종료코드 {result.returncode} ({dt:.1f}초)")
        return False
    print(f"  ✅ [{label}] 완료 ({dt:.1f}초)")
    return True


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    n = 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def _merge_jsonl(paths: list[Path], out_path: Path) -> int:
    """여러 jsonl 머지 + id 기반 dedup."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    seen: set = set()
    n = 0
    with out_path.open("w", encoding="utf-8") as fout:
        for p in paths:
            if not p.exists():
                continue
            with p.open("r", encoding="utf-8") as fin:
                for line in fin:
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
    return n


def collect_g2b(days: int, cache_dir: Path, max_pages: int = 30) -> Path:
    """G2B 수집. G2B API 가 매우 느려서 각 청크의 첫 max_pages(기본 30=3000건)만 sweep."""
    out = cache_dir / "g2b.jsonl"
    _run_module(
        "src.stage0_collect.nara",
        ["--days", str(days), "--output", str(out),
         "--max-pages", str(max_pages)],
        label=f"G2B 나라장터 (각 청크당 {max_pages}페이지 한도)",
        timeout=1800,
    )
    return out


def collect_dart(days: int, cache_dir: Path) -> Path:
    out = cache_dir / "dart.jsonl"
    _run_module(
        "src.stage0_collect.dart",
        ["--days", str(days), "--output", str(out)],
        label="DART 공시",
    )
    return out


def collect_eais(days: int, cache_dir: Path) -> Path:
    out = cache_dir / "eais.jsonl"
    # EAIS 는 동별 1,800회 호출 가능 → quota 한도 1,000/day 고려. 시간 오래 걸림.
    _run_module(
        "src.stage0_collect.eais",
        ["--days", str(days), "--output", str(out)],
        label=f"EAIS 건축인허가 (전국 동 sweep, 시간 소요)",
        timeout=3600,  # 60분
    )
    return out


def collect_rss(days: int, cache_dir: Path) -> Path:
    """RSS 3 tier 모두 수집 → 운영 daily 누적본 머지 → stage1 필터.

    RSS 피드는 매체별 최신 N건만 줘서 며칠 전 기사가 사라지는 문제.
    예: 5/22 한경의 '파마리서치 강릉 5공장 착공' → 며칠 후 RSS 에서 밀려나
    재수집 시 사라짐. 운영 daily 가 매일 받아둔 data/raw/YYYY-MM-DD.jsonl
    (prefix 없는 RSS 파일) 들도 머지에 포함해서 누락 복원.
    """
    raw_paths: list[Path] = []
    for tier in (1, 2, 3):
        raw = cache_dir / f"rss_tier{tier}_raw.jsonl"
        ok = _run_module(
            "src.stage0_collect.run",
            ["--tier", str(tier), "--days", str(days), "--output", str(raw)],
            label=f"RSS tier {tier}",
        )
        if ok and raw.exists():
            raw_paths.append(raw)

    # 운영 daily 누적본도 머지에 포함 (--days 윈도우 내)
    raw_dir = PROJECT_ROOT / "data" / "raw"
    cutoff = datetime.now(KST) - timedelta(days=days)
    daily_added = 0
    if raw_dir.exists():
        for f in sorted(raw_dir.glob("*.jsonl")):
            # RSS daily 파일 패턴: YYYY-MM-DD.jsonl (prefix 없음)
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
    if daily_added:
        print(f"  ➕ 운영 daily RSS 누적본 {daily_added}개 파일 추가 머지")

    # 머지 (id 기반 dedup)
    raw_merged = cache_dir / "rss_raw_merged.jsonl"
    n = _merge_jsonl(raw_paths, raw_merged)
    print(f"  📦 RSS raw 머지: {n}건 → {raw_merged.name}")

    # stage1 필터
    filtered = cache_dir / "rss_filtered.jsonl"
    _run_module(
        "src.stage1_filter.run",
        ["--input", str(raw_merged), "--output", str(filtered)],
        label="RSS stage1 필터",
    )
    return filtered


def main() -> None:
    ap = argparse.ArgumentParser(description="3개월치 테스트 리포트 — 실제 API 재수집")
    ap.add_argument("--days", type=int, default=90,
                    help="윈도우 일수 (기본 90, DART/EAIS/RSS 에 적용)")
    ap.add_argument("--g2b-days", type=int, default=30,
                    help="G2B 윈도우 일수 (기본 30). G2B API 가 매우 느려서 별도 단축. "
                         "0 이면 --days 와 동일.")
    ap.add_argument("--skip", default="",
                    help="건너뛸 단계 콤마 구분 (예: g2b,eais). 캐시된 파일 재사용.")
    ap.add_argument("--only-report", action="store_true",
                    help="수집 단계 모두 생략, 캐시된 데이터로 리포트만 재생성")
    args = ap.parse_args()
    g2b_days = args.g2b_days if args.g2b_days > 0 else args.days

    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    cache_dir = PROJECT_ROOT / "data" / "cache" / "test_3months"
    cache_dir.mkdir(parents=True, exist_ok=True)

    skip = set(s.strip().lower() for s in args.skip.split(",") if s.strip())
    if args.only_report:
        skip = set(ALL_STEPS)

    print(f"🧪 3개월치 테스트 리포트 — 윈도우 {args.days}일 (실제 API 재수집)")
    if g2b_days != args.days:
        print(f"   ※ G2B 는 별도 {g2b_days}일 윈도우 (API 속도 한계)")
    print(f"   생성: {today_str} · 캐시 디렉토리: {cache_dir.relative_to(PROJECT_ROOT)}")
    if skip:
        print(f"   건너뛸 단계: {', '.join(sorted(skip))} (캐시 사용)")

    # === 1) 각 소스 수집 ===
    g2b_path = cache_dir / "g2b.jsonl"
    dart_path = cache_dir / "dart.jsonl"
    eais_path = cache_dir / "eais.jsonl"
    rss_path = cache_dir / "rss_filtered.jsonl"

    if "g2b" not in skip:
        collect_g2b(g2b_days, cache_dir)
    if "dart" not in skip:
        collect_dart(args.days, cache_dir)
    if "eais" not in skip:
        collect_eais(args.days, cache_dir)
    if "rss" not in skip:
        collect_rss(args.days, cache_dir)

    # MFDS — 기존 가장 최신 파일
    raw_dir = PROJECT_ROOT / "data" / "raw"
    mfds_files = sorted(raw_dir.glob("mfds_gmp_*.jsonl"))
    if mfds_files:
        mfds_path = mfds_files[-1]
    else:
        mfds_path = raw_dir / f"mfds_gmp_{today_str}.jsonl"

    # === 2) 수집 결과 카운트 ===
    print()
    print("📊 수집 결과:")
    print(f"   G2B   : {_count_jsonl(g2b_path):>5d}건  ({g2b_path.relative_to(PROJECT_ROOT)})")
    print(f"   DART  : {_count_jsonl(dart_path):>5d}건  ({dart_path.relative_to(PROJECT_ROOT)})")
    print(f"   EAIS  : {_count_jsonl(eais_path):>5d}건  ({eais_path.relative_to(PROJECT_ROOT)})")
    print(f"   RSS   : {_count_jsonl(rss_path):>5d}건  ({rss_path.relative_to(PROJECT_ROOT)})")
    print(f"   MFDS  : {_count_jsonl(mfds_path):>5d}건  ({mfds_path.relative_to(PROJECT_ROOT)}) ← 사용자 지시대로 유지")

    # === 3) daily_report_html.py 호출 ===
    out_html = PROJECT_ROOT / "data" / f"daily_report_TEST_{args.days}DAYS_{today_str}.html"
    print()
    print(f"🛠  daily_report_html.py 호출 (--period-days {args.days})")
    report_cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "daily_report_html.py"),
        "--rss", str(rss_path),
        "--g2b", str(g2b_path),
        "--dart", str(dart_path),
        "--eais", str(eais_path),
        "--mfds", str(mfds_path),
        "--period-days", str(args.days),
        # 세움터(EAIS) 룩백 윈도우/임계값 — 90일 테스트면 90일 윈도우, 운영 임계값(300억) 일치
        "--eais-days", str(args.days),
        "--eais-threshold-eok", "300",
        "--output", str(out_html),
    ]
    result = subprocess.run(report_cmd, capture_output=True, text=True,
                            cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        print("❌ daily_report_html.py 실행 실패:")
        print(result.stderr)
        sys.exit(1)
    print(result.stdout.strip())

    # === 4) HTML 후처리 — "[N일 테스트]" 라벨 ===
    if not out_html.exists():
        print(f"❌ 출력 파일이 생성되지 않음: {out_html}")
        sys.exit(1)
    html = out_html.read_text(encoding="utf-8")
    label_prefix = f"🧪 [{args.days}일 테스트]"
    html = html.replace(
        "<title>자이씨앤에이 수주레이더 —",
        f"<title>{label_prefix} 자이씨앤에이 수주레이더 —",
    )
    html = html.replace(
        "<h1>자이씨앤에이 수주레이더</h1>",
        f'<h1>{label_prefix} 자이씨앤에이 수주레이더 '
        f'<span style="font-size:14px;color:var(--accent-warn);font-weight:400;">'
        f'· 실제 API 재수집 · 필터링 검토용 ({args.days}일 윈도우)</span></h1>',
    )
    out_html.write_text(html, encoding="utf-8")

    print()
    print(f"✅ 테스트 리포트 완성: {out_html}")
    print(f"   브라우저에서 더블클릭으로 열기")


if __name__ == "__main__":
    main()
