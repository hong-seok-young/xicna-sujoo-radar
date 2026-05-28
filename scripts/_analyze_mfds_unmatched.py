"""MFDS 매칭 실패 케이스 패턴 분석.

no_dong 223건이 어떤 패턴인지 → 보완 룰 설계.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.enrich_dongs_from_mfds import (
    parse_addr, load_legal_dong, SNAPSHOT,
)

snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
items = snap["items"]
strict, fallback = load_legal_dong()

# 매칭 실패 케이스
unmatched = []
for it in items:
    addr = it.get("FCTR_ADDR", "") or ""
    sido, sgg, dong = parse_addr(addr)
    if not dong:
        # 도로명 패턴
        unmatched.append({"addr": addr, "sido": sido, "sgg": sgg,
                          "bssh": it.get("BSSH_NM", "")})

print(f"매칭 실패 (no_dong): {len(unmatched)} 건")
print()

# 시도/시군구 분포
print("=== 매칭 실패 시도/시군구 Top 15 ===")
by_loc = Counter(f"{u['sido']} / {u['sgg']}" for u in unmatched)
for loc, n in by_loc.most_common(15):
    print(f"  {n:>3}건  {loc}")
print()

# 주소 패턴 샘플 — 도로명에서 동 이름 추출 가능한지
print("=== 주소 샘플 30건 ===")
for u in unmatched[:30]:
    print(f"  [{u['sgg']:25s}] {u['addr'][:90]}")
print()

# "○○동" 패턴이 도로명/번지 안에 있는 케이스 카운트
patt_dong_in_road = re.compile(r"([가-힣]{2,5}동)\s*\d")  # ○○동 + 번지
n_with_dong_pattern = 0
samples_dong_pattern = []
for u in unmatched:
    m = patt_dong_in_road.search(u["addr"])
    if m:
        n_with_dong_pattern += 1
        if len(samples_dong_pattern) < 10:
            samples_dong_pattern.append((m.group(1), u["addr"]))

print(f"=== '○○동 번지' 패턴 매칭: {n_with_dong_pattern}건 ===")
for d, a in samples_dong_pattern:
    print(f"  추출={d}  주소={a[:90]}")
