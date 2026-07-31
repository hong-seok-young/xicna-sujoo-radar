"""CI 상태 요약기(scripts/ci_status_summary.py) 테스트.

워크플로가 이 출력으로 '부분 실패 vs 완전 실패' 를 판별하고 운영자 메일 본문을
만들기 때문에, 파일이 없거나 깨져도 절대 죽지 않아야 한다(exit 0 + 빈/설명 출력).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ci_status_summary.py"


def _run(status_path, field: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--status", str(status_path), "--field", field],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode, proc.stdout.strip()


def _write(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "run_status.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def test_steps_joined_with_slash(tmp_path):
    p = _write(tmp_path, {"failed_steps": ["[2/7] 나라장터", "[3/7] DART"]})
    rc, out = _run(p, "steps")
    assert rc == 0
    assert out == "[2/7] 나라장터 / [3/7] DART"


def test_steps_empty_when_all_ok(tmp_path):
    p = _write(tmp_path, {"failed_steps": []})
    rc, out = _run(p, "steps")
    assert rc == 0
    assert out == ""


def test_tails_includes_step_name_and_output(tmp_path):
    p = _write(tmp_path, {
        "failed_steps": ["[7/7] HTML 리포트"],
        "failed_tails": {"[7/7] HTML 리포트": ["Traceback (most recent call last):",
                                              "KeyError: 'amount'"]},
    })
    rc, out = _run(p, "tails")
    assert rc == 0
    assert "[7/7] HTML 리포트" in out
    assert "KeyError: 'amount'" in out


def test_missing_file_is_not_an_error(tmp_path):
    rc, out = _run(tmp_path / "nope.json", "steps")
    assert rc == 0
    assert out == ""


def test_broken_json_is_not_an_error(tmp_path):
    p = tmp_path / "run_status.json"
    p.write_text("{not json", encoding="utf-8")
    rc, out = _run(p, "tails")
    assert rc == 0
    assert "파싱 실패" in out
