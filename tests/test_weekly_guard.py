"""주간 발송 감시(ci_weekly_guard) 테스트 — 중복 발송 방지 / 누락 탐지 판정 로직."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

KST = timezone(timedelta(hours=9))
_spec = importlib.util.spec_from_file_location(
    "ci_weekly_guard",
    Path(__file__).resolve().parent.parent / "scripts" / "ci_weekly_guard.py")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def test_kst_date_crosses_midnight():
    # 목 21:40 UTC 예약 = 금 06:40 KST → KST 기준 날짜는 다음날이어야 한다
    assert guard.kst_date("2026-08-27T21:40:00Z") == "2026-08-28"
    assert guard.kst_date("2026-08-28T00:35:00Z") == "2026-08-28"
    assert guard.kst_date("2026-08-27T14:59:00Z") == "2026-08-27"


@pytest.mark.parametrize("now, expected", [
    # 금요일 이른 아침(일일 수집 06:00) — 이번 주 발송 전이므로 지난주 금요일을 본다
    (datetime(2026, 8, 28, 6, 0, tzinfo=KST), "2026-08-21"),
    (datetime(2026, 8, 28, 10, 59, tzinfo=KST), "2026-08-21"),
    # 금 11:00(감시 슬롯) 이후 — 자동 시도 3회가 끝났을 시각이므로 오늘이 대상
    (datetime(2026, 8, 28, 11, 0, tzinfo=KST), "2026-08-28"),
    (datetime(2026, 8, 28, 12, 0, tzinfo=KST), "2026-08-28"),
    (datetime(2026, 8, 28, 23, 59, tzinfo=KST), "2026-08-28"),
    # 토·일·월 — 직전 금요일
    (datetime(2026, 8, 29, 6, 0, tzinfo=KST), "2026-08-28"),
    (datetime(2026, 8, 31, 6, 0, tzinfo=KST), "2026-08-28"),
])
def test_last_completed_friday(now, expected):
    assert guard.last_completed_friday(now) == expected


def _fake_api(runs, jobs_by_run):
    def _api(path, token):
        if "/runs?" in path or path.endswith("/runs"):
            return {"workflow_runs": runs}
        rid = int(path.split("/runs/")[1].split("/")[0])
        return {"jobs": jobs_by_run.get(rid, [])}
    return _api


def _run(rid, num, created):
    return {"id": rid, "run_number": num, "created_at": created}


def _jobs(*steps):
    return [{"steps": [{"name": n, "conclusion": c} for n, c in steps]}]


def test_mail_sent_detects_successful_send(monkeypatch):
    runs = [_run(1, 29, "2026-08-27T21:45:00Z")]
    jobs = {1: _jobs(("Run weekly pipeline", "success"),
                     ("Send email — 짧은 안내 + 첨부파일", "success"))}
    monkeypatch.setattr(guard, "_api", _fake_api(runs, jobs))
    sent, why = guard.mail_sent_on("2026-08-28", "tok", "o/r")
    assert sent and "#29" in why


def test_report_only_run_is_not_a_send(monkeypatch):
    # send_email 미체크 수동 실행: run 은 success 지만 메일 스텝은 skipped
    runs = [_run(2, 30, "2026-08-28T04:28:00Z")]
    jobs = {2: _jobs(("Run weekly pipeline", "success"),
                     ("Send email — 짧은 안내 + 첨부파일", "skipped"))}
    monkeypatch.setattr(guard, "_api", _fake_api(runs, jobs))
    sent, _ = guard.mail_sent_on("2026-08-28", "tok", "o/r")
    assert not sent


def test_other_day_run_ignored(monkeypatch):
    runs = [_run(3, 28, "2026-08-20T21:45:00Z")]
    jobs = {3: _jobs(("Send email — 짧은 안내", "success"))}
    monkeypatch.setattr(guard, "_api", _fake_api(runs, jobs))
    sent, _ = guard.mail_sent_on("2026-08-28", "tok", "o/r")
    assert not sent


def test_self_run_excluded(monkeypatch):
    # 자기 자신(아직 발송 전)을 근거로 skip 하면 영원히 안 나간다
    runs = [_run(9, 31, "2026-08-27T21:45:00Z")]
    jobs = {9: _jobs(("Send email — 짧은 안내", "success"))}
    monkeypatch.setattr(guard, "_api", _fake_api(runs, jobs))
    sent, _ = guard.mail_sent_on("2026-08-28", "tok", "o/r", exclude_run_id="9")
    assert not sent


def test_run_in_flight_suppresses_false_alarm(monkeypatch):
    # 예약이 크게 밀려 감시 시각에 아직 발송이 돌고 있으면 '누락' 알림을 보내면 안 된다
    runs = [dict(_run(4, 33, "2026-08-27T21:45:00Z"), status="in_progress")]
    monkeypatch.setattr(guard, "_api", _fake_api(runs, {}))
    assert guard.run_in_flight("2026-08-28", "tok", "o/r") is True


def test_run_in_flight_false_when_all_completed(monkeypatch):
    runs = [dict(_run(5, 34, "2026-08-27T21:45:00Z"), status="completed")]
    monkeypatch.setattr(guard, "_api", _fake_api(runs, {}))
    assert guard.run_in_flight("2026-08-28", "tok", "o/r") is False
