"""EAIS 캐시 전수 — 임계값/윈도우별 통과 건수 + 상위 추정공사비 리스트.

기존 캐시만 사용 (API 0회). 어느 임계값·윈도우면 실제 영업 리드가
잡히는지 정량 확인용. 일회성 진단 스크립트.
"""
from __future__ import annotations

import csv
import glob
import json
from datetime import datetime, timedelta, timezone

from src.stage0_collect._eais_cost import estimate_cost_man, passes_threshold

KST = timezone(timedelta(hours=9))
now = datetime.now(KST)

# 동 → categories 매핑 로드
dong_cats: dict[str, list[str]] = {}
with open("config/industrial_dongs.csv", encoding="utf-8-sig", newline="") as f:
    for row in csv.DictReader(f):
        key = f"{row['sigungu_cd']}-{row['bjdong_cd']}"
        cats = [c for c in (row.get("categories", "") or "").split("|") if c]
        dong_cats[key] = cats

VALID_GB = ("신축", "증축")
TARGET_PURP = ["공장", "창고", "연구소", "연구시설", "교육연구",
               "제1종근린생활시설", "제2종근린생활시설",
               "발전시설", "위험물", "자원순환", "병원", "의료시설"]

def is_target(p): return any(k in (p or "") for k in TARGET_PURP)
def is_new_ext(g):
    s = (g or "").strip()
    return True if not s else any(k in s for k in VALID_GB)

def sane_day(s):
    """archPmsDay 8자리 + 연도 2000~현재+1 범위만 유효."""
    s = (s or "")[:8]
    if len(s) < 8 or not s.isdigit():
        return None
    y = int(s[:4])
    if y < 2000 or y > now.year + 1:
        return None
    return s

# 캐시 전수 로드 → (day, cost_man, category, title) 후보
cands = []  # (day_str, cost_man, category, purpose, bldnm, plat)
files = glob.glob("data/cache/eais/*_*.json")
garbage_dates = 0
for fp in files:
    if fp.endswith("_index.json"):
        continue
    try:
        d = json.load(open(fp, encoding="utf-8"))
    except Exception:
        continue
    key = f"{d.get('sigungu_cd')}-{d.get('bjdong_cd')}"
    cats = dong_cats.get(key) or None
    for it in d.get("items", []):
        purpose = it.get("mainPurpsCdNm", "") or ""
        if not is_target(purpose):
            continue
        if not is_new_ext(it.get("archGbCdNm", "") or ""):
            continue
        raw_day = (it.get("archPmsDay") or "")[:8]
        day = sane_day(raw_day)
        if day is None:
            if raw_day:
                garbage_dates += 1
            continue
        try:
            area = float(str(it.get("totArea") or "0").replace(",", ""))
        except ValueError:
            area = 0.0
        if area > 1_000_000:
            continue
        cost_man, category, _ = estimate_cost_man(
            purpose, it.get("totArea"), it.get("bldNm", "") or "",
            it.get("platPlc", "") or "", dong_categories=cats)
        cands.append((day, cost_man, category,
                      purpose, (it.get("bldNm") or "")[:20], (it.get("platPlc") or "")[:30]))

print(f"타겟용도+신축/증축 후보: {len(cands)}건 (손상날짜 제외 {garbage_dates}건)")
print()

for win in (7, 30, 90, 365):
    cut = (now - timedelta(days=win)).strftime("%Y%m%d")
    recent = [c for c in cands if c[0] >= cut]
    print(f"=== 최근 {win}일 (>={cut}): 타겟 후보 {len(recent)}건 ===")
    for th in (450, 300, 200, 100, 50):
        th_man = th * 10000
        passed = [c for c in recent if passes_threshold(c[1], th_man)]
        print(f"   {th:>4}억+ 통과: {len(passed)}건")
    print()

# 최근 90일 상위 추정공사비 20건
cut90 = (now - timedelta(days=90)).strftime("%Y%m%d")
recent90 = sorted([c for c in cands if c[0] >= cut90], key=lambda x: -x[1])[:25]
print("=== 최근 90일 추정공사비 상위 25건 ===")
for day, cost_man, cat, purp, bld, plat in recent90:
    eok = cost_man / 10000
    print(f"  {day}  {eok:>8.0f}억  [{cat:8s}] {purp[:12]:12s} {bld:20s} {plat}")
