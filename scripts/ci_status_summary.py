"""run_status.json 요약 출력기 — GitHub Actions 스텝에서 쓰는 작은 도우미.

run_weekly.py 가 남기는 data/logs/run_status.json 에서
  --field steps  → 실패한 단계명 (' / ' 로 연결, 없으면 빈 줄)
  --field tails  → 실패한 단계별 자식 출력 꼬리 (traceback) 텍스트
를 stdout 으로 뽑아 준다. 워크플로 YAML 안에 파이썬을 인라인하지 않기 위해 분리.

사용:
    python scripts/ci_status_summary.py --field steps
    python scripts/ci_status_summary.py --field tails --status data/logs/run_status.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_STATUS = "data/logs/run_status.json"


def main() -> int:
    ap = argparse.ArgumentParser(description="run_status.json 요약 출력")
    ap.add_argument("--status", default=DEFAULT_STATUS, help="상태 JSON 경로")
    ap.add_argument("--field", required=True, choices=["steps", "tails"],
                    help="steps=실패 단계명 / tails=실패 단계 출력 꼬리")
    args = ap.parse_args()

    path = Path(args.status)
    if not path.exists():
        # 상태 파일이 없어도 워크플로를 죽이지 않는다 (빈 출력 + exit 0).
        print("" if args.field == "steps" else "(run_status.json 없음)")
        return 0

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"(run_status.json 파싱 실패: {type(e).__name__}: {e})")
        return 0

    if args.field == "steps":
        print(" / ".join(data.get("failed_steps") or []))
        return 0

    tails: dict[str, list[str]] = data.get("failed_tails") or {}
    if not tails:
        print("(실패 단계의 출력이 비어 있음 — 실패 단계명과 run 로그 확인)")
        return 0
    out: list[str] = []
    for step, lines in tails.items():
        out.append(f"── {step} ──")
        out.extend(lines)
        out.append("")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
