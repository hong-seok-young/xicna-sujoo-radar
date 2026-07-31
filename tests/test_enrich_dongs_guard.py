"""enrich_dongs_from_mfds 방어 로직 테스트 — 스냅샷 없어도 죽지 않아야 한다.

2026-07-31 발사 실패의 실제 원인: MFDS 스냅샷(data/cache/mfds_gmp/snapshot_latest.json)
이 없어 이 스크립트가 FileNotFoundError 로 exit 1 → run_weekly exit 1 →
주간 리포트 메일·Pages 갱신 전부 스킵. 보강할 데이터가 없는 건 에러가 아니다.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "enrich_dongs_from_mfds.py"


def _run(cwd: Path) -> subprocess.CompletedProcess:
    # 스크립트 경로는 cwd 상대 — 빈 임시 디렉터리에서 돌리면 스냅샷도 config 도 없다.
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--apply"], cwd=str(cwd),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
    )


def test_missing_snapshot_exits_zero(tmp_path):
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "스냅샷 없음" in r.stdout


def test_broken_snapshot_exits_zero(tmp_path):
    snap = tmp_path / "data" / "cache" / "mfds_gmp"
    snap.mkdir(parents=True)
    (snap / "snapshot_latest.json").write_text("{broken", encoding="utf-8")
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "읽기 실패" in r.stdout


def test_snapshot_without_items_key_exits_zero(tmp_path):
    snap = tmp_path / "data" / "cache" / "mfds_gmp"
    snap.mkdir(parents=True)
    (snap / "snapshot_latest.json").write_text('{"saved_at": "2026-07-31"}', encoding="utf-8")
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "읽기 실패" in r.stdout
