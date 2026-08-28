"""주간 발송 감시/중복방지 — 예약(schedule) 이벤트 드롭 대비.

배경 (2026-08-28):
  GitHub 이 목 21:40 UTC 주간 예약 이벤트를 아예 발생시키지 않아 run 자체가 없었다.
  run 이 없으면 '실패 알림 메일'도 안 온다 (실패할 run 이 없으니까).
  → 백업 예약 슬롯을 여러 개 두고, 이미 나간 주는 건너뛰는 판정이 필요하다.

두 가지 모드:
  --mode guard     이번 run 이 발송을 진행해야 하나? (오늘 KST 에 이미 발송 성공 run 이
                   있으면 skip=true) — 백업 슬롯 중복 발송 방지.
                   schedule 이벤트가 아니면(수동 dispatch) 항상 진행.
  --mode watchdog  가장 최근 '지나간 금요일'에 발송이 됐는지 점검. 안 됐으면 missing.
                   일일 수집 워크플로가 매일 호출 → 예약이 통째로 드롭돼도 다음 날 알림.

판정 근거는 워크플로 run 의 'Send email' 스텝이 success 인지 (리포트 생성 성공만으로는
발송을 보장 못 함 — send_email 미체크 수동 실행도 success 로 끝난다).

환경변수: GH_TOKEN(또는 GITHUB_TOKEN), GITHUB_REPOSITORY, GITHUB_RUN_ID, GITHUB_EVENT_NAME
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
API = "https://api.github.com"
WEEKLY_WORKFLOW = "weekly-report.yml"
MAIL_STEP_PREFIX = "Send email"   # 워크플로의 영업팀 발송 스텝 이름 접두사

# 금요일 몇 시(KST)부터 '오늘 발송은 끝났어야 한다' 고 볼 것인가.
# weekly 마지막 자동 슬롯이 금 09:40 예약 → GitHub 지연(+25~70분) 감안해도 11:00 전엔 끝난다.
# 이 시각을 넘겨 발송 기록이 없으면 진짜 누락 → 금요일 오전 중에 알린다.
FRIDAY_DEADLINE_HOUR = 11


def _api(path: str, token: str) -> dict:
    req = urllib.request.Request(
        f"{API}{path}",
        headers={"User-Agent": "sujoo-radar-guard",
                 "Accept": "application/vnd.github+json",
                 "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def kst_date(iso_utc: str) -> str:
    """'2026-08-27T21:40:00Z' → KST 기준 'YYYY-MM-DD'."""
    dt = datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).strftime("%Y-%m-%d")


def last_completed_friday(now_kst: datetime) -> str:
    """가장 최근에 '발송 시간대가 지나간' 금요일 (KST, YYYY-MM-DD).

    FRIDAY_DEADLINE_HOUR(11시 KST)를 기준선으로 둔다. 자동 슬롯 3개(06:40·07:40·09:40)가
    지연을 감안해도 끝난 시각이라, 그때도 발송이 없으면 진짜 누락이다.
    금요일 이른 아침(일일 수집 06:00)에는 아직 이번 주 발송 전이므로 지난주 금요일을 본다.
    """
    d = now_kst
    while True:
        if d.weekday() == 4:  # 금요일
            deadline = d.replace(hour=FRIDAY_DEADLINE_HOUR, minute=0, second=0, microsecond=0)
            if now_kst >= deadline:
                return d.strftime("%Y-%m-%d")
        d -= timedelta(days=1)


def mail_sent_on(date_kst: str, token: str, repo: str, exclude_run_id: str = "") -> tuple[bool, str]:
    """해당 KST 날짜에 영업팀 발송(Send email 스텝 success)이 있었나? (여부, 설명)."""
    runs = _api(f"/repos/{repo}/actions/workflows/{WEEKLY_WORKFLOW}/runs?per_page=30",
                token).get("workflow_runs", [])
    checked = 0
    for run in runs:
        if kst_date(run["created_at"]) != date_kst:
            continue
        if exclude_run_id and str(run["id"]) == str(exclude_run_id):
            continue  # 자기 자신은 제외 (아직 발송 전)
        checked += 1
        jobs = _api(f"/repos/{repo}/actions/runs/{run['id']}/jobs", token).get("jobs", [])
        for job in jobs:
            for step in job.get("steps") or []:
                if step["name"].startswith(MAIL_STEP_PREFIX) and step["conclusion"] == "success":
                    return True, f"run #{run['run_number']} 이 {date_kst} 에 발송 완료"
    return False, f"{date_kst} 발송 기록 없음 (해당일 run {checked}건 확인)"


def run_in_flight(date_kst: str, token: str, repo: str, exclude_run_id: str = "") -> bool:
    """해당 KST 날짜에 아직 끝나지 않은 weekly run 이 있나? (있으면 '누락' 판정 보류)

    예약이 크게 지연돼 감시 시각에 발송이 아직 진행 중일 수 있다. 그때 알림을 보내면
    허위 경보가 된다.
    """
    runs = _api(f"/repos/{repo}/actions/workflows/{WEEKLY_WORKFLOW}/runs?per_page=20",
                token).get("workflow_runs", [])
    for run in runs:
        if kst_date(run["created_at"]) != date_kst:
            continue
        if exclude_run_id and str(run["id"]) == str(exclude_run_id):
            continue
        if run["status"] != "completed":
            return True
    return False


def _emit(github_output_lines: list[str]) -> None:
    """GITHUB_OUTPUT / GITHUB_ENV 파일에 기록 (없으면 stdout 만)."""
    for target_env, lines in github_output_lines:
        path = os.environ.get(target_env)
        if not path:
            continue
        with open(path, "a", encoding="utf-8") as f:
            for ln in lines:
                f.write(ln + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="주간 발송 감시/중복방지")
    ap.add_argument("--mode", required=True, choices=["guard", "watchdog"])
    ap.add_argument("--date", default="", help="watchdog 점검 대상 날짜(KST). 기본: 최근 지나간 금요일")
    args = ap.parse_args()

    token = (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token or not repo:
        # 토큰이 없으면 판정 불가 — 발송을 막지 않는다(안전한 쪽: 그냥 진행).
        print("GH_TOKEN/GITHUB_REPOSITORY 없음 — 판정 생략")
        _emit([("GITHUB_OUTPUT", ["skip=false"]), ("GITHUB_ENV", ["WEEKLY_MISSING=0"])])
        return 0

    now_kst = datetime.now(timezone.utc).astimezone(KST)

    if args.mode == "guard":
        event = os.environ.get("GITHUB_EVENT_NAME", "")
        if event != "schedule":
            print(f"이벤트={event or '(미상)'} — 예약 실행이 아니므로 중복 판정 생략, 진행")
            _emit([("GITHUB_OUTPUT", ["skip=false"])])
            return 0
        today = now_kst.strftime("%Y-%m-%d")
        try:
            sent, why = mail_sent_on(today, token, repo, os.environ.get("GITHUB_RUN_ID", ""))
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError) as e:
            # 판정 실패 시에도 발송을 막지 않는다 (누락 > 중복)
            print(f"판정 실패({type(e).__name__}: {e}) — 안전하게 진행")
            _emit([("GITHUB_OUTPUT", ["skip=false"])])
            return 0
        print(("이미 발송됨 → 이번 run 은 건너뜀: " if sent else "미발송 → 진행: ") + why)
        _emit([("GITHUB_OUTPUT", [f"skip={'true' if sent else 'false'}"])])
        return 0

    # watchdog
    target = args.date or last_completed_friday(now_kst)
    try:
        sent, why = mail_sent_on(target, token, repo)
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError) as e:
        print(f"점검 실패({type(e).__name__}: {e}) — 알림 생략")
        _emit([("GITHUB_ENV", ["WEEKLY_MISSING=0"])])
        return 0
    if not sent and run_in_flight(target, token, repo):
        print(f"보류: {target} weekly run 이 아직 진행 중 — 알림 생략")
        _emit([("GITHUB_ENV", ["WEEKLY_MISSING=0"])])
        return 0
    print(("정상: " if sent else "누락 감지: ") + why)
    _emit([("GITHUB_ENV", [f"WEEKLY_MISSING={'0' if sent else '1'}",
                           f"WEEKLY_MISSING_DATE={target}"])])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
