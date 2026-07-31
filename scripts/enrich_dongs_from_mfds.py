"""MFDS GMP 공장주소 → 법정동 매칭 → industrial_dongs.csv 제약/바이오 동 보강.

전략:
  1. FCTR_ADDR 에서 (시도, 시군구, 읍/면/동) 추출 — 정규식
  2. legal_dong.csv 와 (시도, 시군구, 읍/면/동) 매칭 → sigunguCd + bjdongCd 부여
  3. 매칭 성공 건 중 industrial_dongs.csv 에 없는 동 = 보강 후보
  4. 동별 GMP 보유 수 분포 → 5건+ 만 후보 추천 (단발 X)
  5. 옵션 --apply: industrial_dongs.csv 에 제약_바이오 카테고리로 추가

  python scripts/enrich_dongs_from_mfds.py             # dry-run (분석만)
  python scripts/enrich_dongs_from_mfds.py --apply     # 실제 csv 업데이트
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

SNAPSHOT = Path("data/cache/mfds_gmp/snapshot_latest.json")
LEGAL_DONG = Path("config/legal_dong.csv")
INDUSTRIAL_DONG = Path("config/industrial_dongs.csv")
ENRICH_REPORT = Path("data/cache/mfds_gmp/enrich_report.json")

# 행정동·법정동·도로명 혼재 주소에서 읍/면/(동/리)+ 추출
# 우선순위:
#   1) 도로명 후 명시적 읍/면/동/리 키워드 + 공백
#   2) 도로명주소 끝에 괄호로 명시된 동 — "(○○동)", "(○○동, ...)" 패턴
#      ex) "경기도 파주시 문발로 320 1동 1층 (문발동)"
#          "대전 서구 관저동로 158 (관저동, 건양대학교병원)"
PATTERN_DONG_LIKE = re.compile(r"([가-힣]+(?:읍|면|동|리))\s")
PATTERN_DONG_IN_PAREN = re.compile(r"\(([가-힣]{2,8}(?:동|리))[\s,\)]")


def parse_addr(addr: str) -> tuple[str, str, str]:
    """주소 → (sido, sigungu, dong_or_eubmyeon).

    sigungu 는 split 자치구 포함 형태로 ("경기도 수원시 장안구" 같이).
    """
    addr = (addr or "").strip()
    parts = addr.split()
    if len(parts) < 3:
        return ("", "", "")

    sido = parts[0]

    # 시군구 — 자치구 있으면 2토큰, 없으면 1토큰
    # ex) "경기도 수원시 장안구" / "경기도 화성시" / "충청북도 청주시 흥덕구"
    # token[1] 이 "○○시" 면 sigungu, token[2] 가 "○○구" 면 자치구 포함
    sigungu_parts = [parts[1]]
    if len(parts) > 2 and parts[2].endswith("구"):
        sigungu_parts.append(parts[2])
    sigungu = " ".join(sigungu_parts)

    # 동/읍/면 — 정규식으로
    m = PATTERN_DONG_LIKE.search(addr)
    dong = m.group(1) if m else ""

    # fallback: 도로명주소 끝에 괄호로 동 명시된 케이스
    # ex) "○○로 N (문발동)" / "○○로 N (관저동, 건양대병원)"
    if not dong:
        m2 = PATTERN_DONG_IN_PAREN.search(addr)
        if m2:
            dong = m2.group(1)

    return (sido, sigungu, dong)


def load_legal_dong() -> tuple[
    dict[tuple[str, str, str], tuple[str, str]],
    dict[tuple[str, str, str], list[tuple[str, str]]],
]:
    """legal_dong.csv → 2가지 인덱스 반환.

    1) strict: {(sido, sigungu_full(자치구포함), dong): (sigunguCd, bjdongCd)}
       - 자치구 포함 정확 매칭용 (1순위)
    2) fallback: {(sido, sigungu_no_gu, dong): [(sigunguCd, bjdongCd), ...]}
       - 자치구 없이 (예: "화성시 향남읍") 매칭. 자치구 split 도시는 후보 여러 개,
         1개만 있을 때 안전하게 매핑 가능.
    """
    if not LEGAL_DONG.exists():
        raise FileNotFoundError(LEGAL_DONG)
    strict: dict[tuple[str, str, str], tuple[str, str]] = {}
    fallback: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
    with LEGAL_DONG.open("r", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            sido = r.get("sido", "").strip()
            sigungu = r.get("sigungu", "").strip()
            gu = (r.get("gu") or "").strip()
            dong = r.get("dong", "").strip()
            codes = (r.get("sigungu_cd", ""), r.get("bjdong_cd", ""))

            sgg_full = f"{sigungu} {gu}".strip()
            strict[(sido, sgg_full, dong)] = codes

            # fallback — 자치구 빼고 시군구만
            key_fb = (sido, sigungu, dong)
            fallback.setdefault(key_fb, []).append(codes)
    return strict, fallback


def load_industrial() -> tuple[dict[tuple[str, str], list[str]], list[dict]]:
    """industrial_dongs.csv → ({(sigunguCd, bjdongCd): [cats]}, raw rows)."""
    if not INDUSTRIAL_DONG.exists():
        return {}, []
    cat_map: dict[tuple[str, str], list[str]] = {}
    rows: list[dict] = []
    with INDUSTRIAL_DONG.open("r", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            key = (r["sigungu_cd"], r["bjdong_cd"])
            cats = [c for c in (r.get("categories") or "").split("|") if c]
            cat_map[key] = cats
            rows.append(r)
    return cat_map, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="실제 industrial_dongs.csv 에 제약_바이오 추가 (dry-run 안 함)")
    ap.add_argument("--min-count", type=int, default=2,
                    help="동별 GMP 보유 수 임계 (기본 2 — 단발성 X)")
    args = ap.parse_args()

    # 스냅샷은 MFDS 수집 단계(src/stage0_collect/mfds_gmp.py)가 남긴다.
    # 그 단계가 키 누락·API 장애로 건너뛰어지면 파일이 없다 — 이건 '보강할 새 데이터가
    # 없음' 이지 에러가 아니다. 예전엔 여기서 FileNotFoundError 로 죽어 파이프라인이
    # exit 1 → 주간 발송 전체가 스킵됐다 (2026-07-31 발사 실패 원인).
    if not SNAPSHOT.exists():
        print(f"MFDS 스냅샷 없음 ({SNAPSHOT}) - 보강 생략 (MFDS 수집 단계 확인 필요)")
        return
    try:
        snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        items = snap["items"]
    except (json.JSONDecodeError, KeyError, OSError) as e:
        print(f"MFDS 스냅샷 읽기 실패 ({type(e).__name__}: {e}) - 보강 생략")
        return
    print(f"MFDS 스냅샷 {len(items)} 건 로드")

    legal, legal_fb = load_legal_dong()
    print(f"legal_dong strict {len(legal)} / fallback {len(legal_fb)} 키 로드")

    cat_map, existing_rows = load_industrial()
    print(f"industrial_dongs {len(existing_rows)} 동 로드")
    print()

    # 1) 주소 파싱 — 동 단위 매칭 성공률
    parsed_keys = []
    for it in items:
        addr = it.get("FCTR_ADDR", "")
        sido, sgg, dong = parse_addr(addr)
        parsed_keys.append((sido, sgg, dong, addr))

    n_no_dong = sum(1 for s, g, d, _ in parsed_keys if not d)
    n_with_dong = len(parsed_keys) - n_no_dong
    print(f"동 단위 추출 성공: {n_with_dong}/{len(parsed_keys)} "
          f"({100*n_with_dong/len(parsed_keys):.0f}%)")

    # 2) legal_dong 매칭 — strict (자치구 포함) → fallback (자치구 빼고, 후보 1개일 때만)
    matched = []   # [(sigunguCd, bjdongCd, sido, sgg, dong, addr)]
    unmatched = []
    n_fb_used = 0  # fallback 으로 매칭된 건수
    for sido, sgg, dong, addr in parsed_keys:
        if not dong:
            unmatched.append((sido, sgg, dong, addr, "no_dong"))
            continue
        # strict 우선
        codes = legal.get((sido, sgg, dong))
        if codes:
            matched.append((codes[0], codes[1], sido, sgg, dong, addr))
            continue
        # fallback — sgg 가 "화성시 만세구" 처럼 자치구 포함이지만 legal_dong 표기 다른 경우
        # sgg 첫 토큰("화성시") 만 추출해서 재시도. 후보 1개일 때만 안전.
        sgg_no_gu = sgg.split()[0] if sgg else ""
        candidates = legal_fb.get((sido, sgg_no_gu, dong), [])
        if len(candidates) == 1:
            matched.append((candidates[0][0], candidates[0][1], sido, sgg, dong, addr))
            n_fb_used += 1
        elif len(candidates) > 1:
            unmatched.append((sido, sgg, dong, addr, "ambiguous_fb"))
        else:
            unmatched.append((sido, sgg, dong, addr, "not_in_legal"))

    print(f"legal_dong 매칭 성공: {len(matched)}/{len(parsed_keys)} "
          f"({100*len(matched)/len(parsed_keys):.0f}%)  "
          f"(fallback 사용 {n_fb_used}건)")
    print()

    # 3) 매칭된 동별 GMP 카운트
    by_dong = Counter()
    by_dong_sample = {}
    for sigCd, bjCd, sido, sgg, dong, addr in matched:
        key = (sigCd, bjCd)
        by_dong[key] += 1
        by_dong_sample[key] = (sido, sgg, dong)

    print(f"매칭된 동: {len(by_dong)}개")
    print()

    # 4) 기존 vs 신규 (industrial_dongs 기준)
    in_existing = []
    new_candidates = []
    for key, cnt in by_dong.most_common():
        sido, sgg, dong = by_dong_sample[key]
        existing_cats = cat_map.get(key)
        if existing_cats is not None:
            has_bio = "제약_바이오" in existing_cats
            in_existing.append((key, cnt, sido, sgg, dong, existing_cats, has_bio))
        else:
            new_candidates.append((key, cnt, sido, sgg, dong))

    # 4a) 기존 동 중 제약_바이오 카테고리 없는 동 → 카테고리 추가 후보
    add_category_candidates = [
        (key, cnt, sido, sgg, dong, cats)
        for key, cnt, sido, sgg, dong, cats, has_bio in in_existing
        if not has_bio and cnt >= args.min_count
    ]

    # 4b) 아예 없는 동 → 신규 추가 후보
    new_candidates_filt = [c for c in new_candidates if c[1] >= args.min_count]

    print("=" * 70)
    print(f"📊 분석 결과 (min-count={args.min_count})")
    print("=" * 70)
    print(f"  기존 industrial_dongs 에 이미 있음: {len(in_existing)} 동")
    print(f"     ├ 제약_바이오 카테고리 보유: {sum(1 for *_, has in in_existing if has)} 동")
    print(f"     └ 제약_바이오 카테고리 없음:  {sum(1 for *_, has in in_existing if not has)} 동")
    print(f"  industrial_dongs 에 없음 (신규 후보): {len(new_candidates)} 동")
    print()
    print(f"📌 카테고리 추가 후보 (기존 동, 제약_바이오 없음, ≥{args.min_count}건):")
    for key, cnt, sido, sgg, dong, cats in add_category_candidates[:30]:
        print(f"    {cnt:>3}건  {sido} {sgg} {dong}  (현재: {'|'.join(cats)})")
    if len(add_category_candidates) > 30:
        print(f"    ... 외 {len(add_category_candidates)-30}건")
    print()
    print(f"🆕 신규 동 추가 후보 (없는 동, ≥{args.min_count}건):")
    for key, cnt, sido, sgg, dong in new_candidates_filt[:30]:
        print(f"    {cnt:>3}건  {sido} {sgg} {dong}  (sgg_cd={key[0]}, bj_cd={key[1]})")
    if len(new_candidates_filt) > 30:
        print(f"    ... 외 {len(new_candidates_filt)-30}건")
    print()

    # 5) 매칭 실패 진단 (상위 사례)
    unm_by_reason = Counter(r for *_, r in unmatched)
    print(f"매칭 실패: {len(unmatched)} 건")
    for reason, n in unm_by_reason.most_common():
        print(f"  {reason}: {n}")
    print()
    print(f"  not_in_legal 샘플 (도로명만 있고 동 키워드 X 또는 legal_dong 에 없음):")
    not_in_legal_samples = [(s, g, d, a) for s, g, d, a, r in unmatched if r == "not_in_legal"][:10]
    for sido, sgg, dong, addr in not_in_legal_samples:
        print(f"    [{sido}|{sgg}|{dong}]  {addr[:60]}")
    print()

    # 6) 보고서 저장
    report = {
        "total_mfds": len(items),
        "dong_extracted": n_with_dong,
        "legal_matched": len(matched),
        "by_dong_distinct": len(by_dong),
        "add_category_candidates": [
            {"sigungu_cd": k[0], "bjdong_cd": k[1], "count": cnt,
             "sido": s, "sigungu": g, "dong": d, "current_cats": cats}
            for k, cnt, s, g, d, cats in add_category_candidates
        ],
        "new_candidates": [
            {"sigungu_cd": k[0], "bjdong_cd": k[1], "count": cnt,
             "sido": s, "sigungu": g, "dong": d}
            for k, cnt, s, g, d in new_candidates_filt
        ],
    }
    ENRICH_REPORT.parent.mkdir(parents=True, exist_ok=True)
    ENRICH_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    print(f"분석 보고서 저장: {ENRICH_REPORT}")
    print()

    if not args.apply:
        print("=" * 70)
        print("DRY-RUN 모드 — --apply 옵션으로 실제 csv 업데이트")
        print("=" * 70)
        return

    # 7) --apply: industrial_dongs.csv 업데이트
    print("=" * 70)
    print(f"🔧 industrial_dongs.csv 업데이트 적용 중...")
    print("=" * 70)

    # 기존 행 dict-of-row 로 변환 (sigungu_cd+bjdong_cd 키)
    by_key = {(r["sigungu_cd"], r["bjdong_cd"]): r for r in existing_rows}

    # 7a) 기존 동 — 제약_바이오 카테고리 추가
    n_added_cat = 0
    for key, cnt, sido, sgg, dong, cats in add_category_candidates:
        row = by_key[key]
        existing_cats = [c for c in (row.get("categories") or "").split("|") if c]
        if "제약_바이오" not in existing_cats:
            existing_cats.append("제약_바이오")
            row["categories"] = "|".join(existing_cats)
            n_added_cat += 1

    # 7b) 신규 동 추가
    n_new = 0
    for key, cnt, sido, sgg, dong in new_candidates_filt:
        sigungu_only = sgg.split()[0] if sgg else ""
        gu_only = sgg.split()[1] if len(sgg.split()) >= 2 else ""
        full_nm = f"{sido} {sgg} {dong}".strip()
        new_row = {
            "sigungu_cd": key[0],
            "bjdong_cd": key[1],
            "sido": sido,
            "sigungu": sigungu_only,
            "gu": gu_only,
            "dong": dong,
            "full_nm": full_nm,
            "categories": "제약_바이오",
        }
        existing_rows.append(new_row)
        n_new += 1

    # 7c) 파일 재저장 (동 정렬 그대로)
    fieldnames = ["sigungu_cd", "bjdong_cd", "sido", "sigungu", "gu",
                  "dong", "full_nm", "categories"]
    with INDUSTRIAL_DONG.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in existing_rows:
            # 누락 필드 안전 처리
            w.writerow({k: r.get(k, "") for k in fieldnames})

    print(f"  ✓ 카테고리 추가 (제약_바이오): {n_added_cat} 동")
    print(f"  ✓ 신규 동 추가: {n_new} 동")
    print(f"  ✓ 총 industrial_dongs 행수: {len(existing_rows)} 동")
    print(f"  → {INDUSTRIAL_DONG}")


if __name__ == "__main__":
    main()
