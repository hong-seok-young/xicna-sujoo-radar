"""MFDS GMP 스냅샷 분석 — 산업동 보강 잠재력 평가."""
import json
import re
from collections import Counter
from pathlib import Path

SNAPSHOT = Path("data/cache/mfds_gmp/snapshot_latest.json")


def sido_from_addr(addr: str) -> str:
    addr = (addr or "").strip()
    if not addr:
        return "(주소없음)"
    return addr.split()[0] if addr else "?"


def sigungu_from_addr(addr: str) -> str:
    parts = (addr or "").split()
    if len(parts) < 2:
        return "?"
    return f"{parts[0]} {parts[1]}"


def main():
    d = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    items = d["items"]
    print(f"총 {len(items)}건  snapshot_at={d['snapshot_at']}")
    print()

    # 1) 응답 필드 종합 — 누락 없이 모든 키 수집
    all_keys = Counter()
    for it in items:
        for k in it.keys():
            all_keys[k] += 1
    print(f"=== 응답 필드 (전체 {len(items)}건 중) ===")
    for k, n in all_keys.most_common():
        sample = next((it[k] for it in items if it.get(k)), None)
        print(f"  {k:30s}  {n:>3}건  e.g. {str(sample)[:50]}")
    print()

    # 2) 시도별 분포
    print("=== 시도별 분포 ===")
    by_sido = Counter(sido_from_addr(it.get("FCTR_ADDR", "")) for it in items)
    for s, n in by_sido.most_common():
        print(f"  {s:15s} {n:>3}건")
    print()

    # 3) 시군구 Top 25
    print("=== 시군구 Top 25 ===")
    by_sgg = Counter(sigungu_from_addr(it.get("FCTR_ADDR", "")) for it in items)
    for s, n in by_sgg.most_common(25):
        print(f"  {s:30s} {n:>3}건")
    print()

    # 4) 회사 Top 20 (한 회사가 여러 GMP 보유 가능)
    print("=== 업체 Top 20 ===")
    by_bssh = Counter((it.get("BSSH_NM") or "").strip() for it in items)
    for s, n in by_bssh.most_common(20):
        print(f"  {s:35s} {n:>3}건")
    print()

    # 5) 완제 vs 원료 분포
    print("=== 완제/원료 구분 ===")
    by_kind = Counter((it.get("KGMP_BGMP_NAME") or "?").strip() for it in items)
    for s, n in by_kind.most_common():
        print(f"  {s:20s} {n:>3}건")
    print()

    # 6) BIZRNO 가 모든 항목에 있는지
    n_bizrno = sum(1 for it in items if (it.get("BIZRNO") or "").strip())
    print(f"BIZRNO 있는 항목: {n_bizrno}/{len(items)}")

    # 7) 유효기간 분포 — 2026/2027/... 별
    print()
    print("=== 유효기간 만료 연도 분포 ===")
    by_vld_year = Counter()
    for it in items:
        vld = (it.get("VLD_PRD_YMD") or "")
        m = re.match(r"^(\d{4})", vld)
        by_vld_year[m.group(1) if m else "?"] += 1
    for y, n in sorted(by_vld_year.items()):
        print(f"  {y}  {n:>3}건")


if __name__ == "__main__":
    main()
