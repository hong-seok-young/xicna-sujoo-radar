"""산업 시군구 화이트리스트 × 법정동 CSV → 산업 후보 동 CSV 생성.

입력: config/industrial_sigungu.yaml + config/legal_dong.csv
출력: config/industrial_dongs.csv (~1,000개 동 예상)

사용법: python scripts/build_industrial_dongs.py
"""
from __future__ import annotations

import csv
import difflib
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

WHITELIST_YAML = Path("config/industrial_sigungu.yaml")
LEGAL_DONG_CSV = Path("config/legal_dong.csv")
OUT_CSV = Path("config/industrial_dongs.csv")


# ─── 시도 명칭 별칭 (행안부 명칭 변경·줄임말 흡수) ──────────────
SIDO_ALIASES: dict[str, str] = {
    "강원도": "강원특별자치도",
    "전라북도": "전북특별자치도",
    "전북": "전북특별자치도",
    "제주도": "제주특별자치도",
    "제주": "제주특별자치도",
    "서울": "서울특별시",
    "부산": "부산광역시",
    "대구": "대구광역시",
    "인천": "인천광역시",
    "광주": "광주광역시",
    "대전": "대전광역시",
    "울산": "울산광역시",
    "세종": "세종특별자치시",
    "세종시": "세종특별자치시",
    "경기": "경기도",
    "강원": "강원특별자치도",
    "충북": "충청북도",
    "충남": "충청남도",
    "전남": "전라남도",
    "경북": "경상북도",
    "경남": "경상남도",
}

# "용인시처인구" / "용인시 처인구" / "성남시분당구" → "용인시" / "성남시"
_SUBDIV_PATTERN = re.compile(r"^(.+?시)\s*[가-힣]+구$")


def normalize_sido(sido: str) -> str:
    s = sido.strip()
    return SIDO_ALIASES.get(s, s)


def normalize_sigungu(sigungu: str) -> str:
    s = sigungu.strip()
    m = _SUBDIV_PATTERN.match(s)
    if m:
        return m.group(1)
    return s


def load_whitelist() -> tuple[set[tuple[str, str]], dict[tuple[str, str], list[str]]]:
    """(sido, sigungu) 셋 + 각 시군구가 어느 카테고리에 속하는지. 별칭/자치구 자동 정규화."""
    data = yaml.safe_load(WHITELIST_YAML.read_text(encoding="utf-8"))
    wl: set[tuple[str, str]] = set()
    cat_map: dict[tuple[str, str], list[str]] = {}
    fix_log: list[tuple[str, str, str, str]] = []  # (원본 sido, 원본 sigungu, 정규화 sido, 정규화 sigungu)
    for category, entries in data.items():
        for e in entries:
            raw_sido = e["sido"]
            raw_sigungu = e["sigungu"]
            sido = normalize_sido(raw_sido)
            sigungu = normalize_sigungu(raw_sigungu)
            if (sido, sigungu) != (raw_sido, raw_sigungu):
                fix_log.append((raw_sido, raw_sigungu, sido, sigungu))
            key = (sido, sigungu)
            wl.add(key)
            cat_map.setdefault(key, []).append(category)
    if fix_log:
        print(f"⚙️  화이트리스트 자동 정규화 {len(fix_log)}건:")
        for rs, rg, ns, ng in fix_log:
            print(f"    '{rs} {rg}' → '{ns} {ng}'")
        print()
    return wl, cat_map


def load_legal_dongs() -> list[dict]:
    with LEGAL_DONG_CSV.open("r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main():
    wl, cat_map = load_whitelist()
    print(f"화이트리스트 시군구: {len(wl)}개")

    legal = load_legal_dongs()
    print(f"전국 동/읍/면: {len(legal)}개")

    # 매칭
    matched: list[dict] = []
    matched_keys: set[tuple[str, str]] = set()
    for r in legal:
        key = (r["sido"], r["sigungu"])
        if key in wl:
            matched.append({
                "sigungu_cd": r["sigungu_cd"],
                "bjdong_cd": r["bjdong_cd"],
                "sido": r["sido"],
                "sigungu": r["sigungu"],
                "gu": r.get("gu", ""),
                "dong": r["dong"],
                "full_nm": r["full_nm"],
                "categories": "|".join(cat_map[key]),
            })
            matched_keys.add(key)

    # ── 컷: 읍/면은 全 포함, 동만 (시군구·자치구) 단위 cap ──
    # legal_dong.csv 의 gu 컬럼으로 자치구 구분 → 가나다순 자치구 편중 문제 해결.
    # 창원 의창구·성산구·진해구·마산합포구·마산회원구 각각 별도 cap 적용.
    DONG_CAP = 15  # 자치구당 동 cap (자치구 5개면 시군구 합계 75개)

    def _is_eup_myeon(dong: str) -> bool:
        d = (dong or "").strip()
        if not d or " " in d:
            return False
        return d.endswith("읍") or d.endswith("면")

    # (sido, sigungu, gu) 그룹화 — 자치구 없으면 gu="" 로 1그룹.
    by_sgg: dict[tuple[str, str, str], list[dict]] = {}
    for r in matched:
        by_sgg.setdefault((r["sido"], r["sigungu"], r.get("gu", "")), []).append(r)

    capped: list[dict] = []
    n_eup_myeon = 0
    n_dong_total = 0
    n_dong_kept = 0
    for (sido, sigungu, gu), group in by_sgg.items():
        eup_myeon = [r for r in group if _is_eup_myeon(r["dong"])]
        dongs = [r for r in group if not _is_eup_myeon(r["dong"])]
        dongs.sort(key=lambda r: r["dong"])
        cats = cat_map[(sido, sigungu)]
        high_value = any(c in ("반도체_디스플레이", "이차전지") for c in cats)
        cap = DONG_CAP + (10 if high_value else 0)
        dongs_kept = dongs[:cap]
        capped.extend(eup_myeon)
        capped.extend(dongs_kept)
        n_eup_myeon += len(eup_myeon)
        n_dong_total += len(dongs)
        n_dong_kept += len(dongs_kept)
    print()
    print(f"컷 결과: 읍/면 {n_eup_myeon}개 (全 포함) + 동 {n_dong_kept}/{n_dong_total}개")
    print(f"        cap={DONG_CAP}/자치구 (CR/이차전지 +10), 합계 {len(capped)}개")
    matched = capped

    print(f"매칭된 시군구: {len(matched_keys)}개 (화이트리스트의 {len(matched_keys)}/{len(wl)})")
    print(f"매칭된 동: {len(matched)}개")

    # 미매칭 시군구 (오타·명칭 불일치 의심) — fuzzy 후보 제안
    missing = wl - matched_keys
    if missing:
        # legal_dong.csv 안의 (sido, sigungu) 모음 — fuzzy 검색용
        legal_sg: dict[str, list[str]] = {}
        legal_full: list[str] = []
        for r in legal:
            legal_sg.setdefault(r["sido"], [])
            if r["sigungu"] not in legal_sg[r["sido"]]:
                legal_sg[r["sido"]].append(r["sigungu"])
            full = f"{r['sido']} {r['sigungu']}"
            if full not in legal_full:
                legal_full.append(full)

        print()
        print(f"⚠️  매칭 안 된 화이트리스트 {len(missing)}개 — 유사 후보 제안:")
        for sido, sigungu in sorted(missing):
            print(f"    ✗ {sido} {sigungu}")
            # 1) 같은 sido 안에서 sigungu 후보
            sg_pool = legal_sg.get(sido, [])
            sg_candidates = difflib.get_close_matches(sigungu, sg_pool, n=3, cutoff=0.5)
            if sg_candidates:
                hits = ", ".join(sg_candidates)
                print(f"        → 같은 시도에서 유사: {hits}")
            # 2) 전국 풀에서 'sido sigungu' 통째 후보
            full_q = f"{sido} {sigungu}"
            full_candidates = difflib.get_close_matches(full_q, legal_full, n=3, cutoff=0.5)
            if full_candidates:
                hits = ", ".join(full_candidates)
                print(f"        → 전국에서 유사: {hits}")
            if not sg_candidates and not full_candidates:
                print(f"        → 후보 없음 (오타 가능성 또는 신설 행정구역)")

    # 시군구별 동 개수 (가장 큰 거 Top 15)
    print()
    print("시군구별 동 개수 (Top 15):")
    cnt = Counter((r["sido"], r["sigungu"]) for r in matched)
    for (sido, sigungu), n in cnt.most_common(15):
        cats = "|".join(cat_map[(sido, sigungu)])
        print(f"  {sido:14s} {sigungu:14s} {n:>4}개  [{cats}]")

    # 저장
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "sigungu_cd", "bjdong_cd", "sido", "sigungu", "gu", "dong", "full_nm", "categories",
        ])
        w.writeheader()
        w.writerows(matched)
    print()
    print(f"✓ {OUT_CSV} 저장 ({len(matched)}개 동)")


if __name__ == "__main__":
    main()
