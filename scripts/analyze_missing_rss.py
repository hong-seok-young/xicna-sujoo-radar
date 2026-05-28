"""RSS raw 1856건 중 stage1 안 통과한 1728건에 영업가치 있는 거 묻혔나 점검.

목적: false positive 외에 false negative (놓침) 도 확인.
사용:
  python scripts/analyze_missing_rss.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.stage1_filter.filter import evaluate  # noqa: E402
from src.common.schema import Article  # noqa: E402

# 영업 가치 시그널 — 이게 들어가있는데 stage1 떨어진 건 의심
SIGNAL_KEYWORDS = [
    "공장", "신축", "착공", "증설", "기공", "물류센터", "데이터센터",
    "연구소", "R&D센터", "캠퍼스", "GMP", "클린룸", "반도체 공장",
    "이차전지 공장", "팹", "fab", "FAB",
    "투자 결정", "투자 발표", "투자 계획", "건설 계획", "조성",
    "토지 매입", "부지 매입", "용지 매입", "건축 허가",
    "준공", "완공",  # 준공 후도 일단은 시그널 (나중에 분석)
]

# 약한 시그널 (제조시설 관련 단어)
WEAK_SIGNAL_KEYWORDS = [
    "공정", "라인", "설비", "양산", "생산능력", "캐파", "capacity",
]


def main():
    raw_path = PROJECT_ROOT / "data" / "cache" / "test_3months" / "rss_raw_merged.jsonl"
    items = []
    for line in raw_path.open(encoding="utf-8"):
        line = line.strip()
        if line:
            items.append(json.loads(line))
    print(f"[input] {raw_path.relative_to(PROJECT_ROOT)}: {len(items)}건")

    # stage1 평가
    passed = []
    failed = []
    for it in items:
        # raw item -> Article
        a = Article(
            id=it.get("id", ""),
            source=it.get("source", ""),
            url=it.get("url", ""),
            title=it.get("title", ""),
            content=it.get("content", ""),
            published_at=None,
        )
        result = evaluate(a)
        if result.passed:
            passed.append((a, result))
        else:
            failed.append((a, result))

    print(f"stage1 통과: {len(passed)}건")
    print(f"stage1 탈락: {len(failed)}건")

    # 탈락 사유 분포
    reason_counter = Counter()
    for a, r in failed:
        reason_counter[r.exclude_reason] += 1
    print("\n[탈락 사유 분포]")
    for reason, n in reason_counter.most_common():
        print(f"  {reason:30}: {n:4d}건")

    # 탈락 1728건 중 SIGNAL_KEYWORDS 들어가있는 거 = false negative 후보
    suspects = []
    for a, r in failed:
        text = f"{a.title}\n{a.content[:500]}"
        strong_hits = [k for k in SIGNAL_KEYWORDS if k in text]
        weak_hits = [k for k in WEAK_SIGNAL_KEYWORDS if k in text]
        if strong_hits:
            suspects.append({
                "title": a.title,
                "source": a.source,
                "url": a.url,
                "content_head": a.content[:200],
                "signals": strong_hits,
                "weak": weak_hits,
                "reason": r.exclude_reason,
                "matched_rules": r.matched_rules,
                "matched_actions": r.matched_actions,
                "matched_targets": r.matched_targets,
                "matched_money": r.matched_money,
                "matched_area": r.matched_area,
                "matched_excludes": r.matched_excludes,
            })
    print(f"\n[잠재 false negative] 탈락했지만 시공 관련 키워드 포함: {len(suspects)}건")

    # 시그널 카테고리별로 그룹
    by_signal = Counter()
    for s in suspects:
        for k in s["signals"]:
            by_signal[k] += 1
    print("\n[시그널 키워드 빈도]")
    for k, n in by_signal.most_common(20):
        print(f"  {k:15}: {n:4d}건")

    # 탈락 사유별 샘플 (각 사유에서 시그널 가진 거 우선)
    out_path = PROJECT_ROOT / "data" / "cache" / "test_3months" / "rss_missing_review.txt"
    suspects_by_reason: dict[str, list] = {}
    for s in suspects:
        suspects_by_reason.setdefault(s["reason"], []).append(s)

    with out_path.open("w", encoding="utf-8") as f:
        f.write(f"=== RSS 누락 후보 점검 ===\n")
        f.write(f"raw 총 {len(items)}건 / 탈락 {len(failed)}건 / 시공 시그널 있는 탈락 {len(suspects)}건\n\n")

        # no_rule_matched 그룹 (가장 큰 카테고리)
        for reason in ("no_rule_matched", "strong_exclude", "soft_exclude"):
            group = [s for s in suspects if s["reason"].startswith(reason)]
            f.write(f"\n{'='*70}\n== {reason}  ({len(group)}건)\n{'='*70}\n\n")
            for i, s in enumerate(group[:50], 1):
                f.write(f"[{i:3d}] {s['source']} | 시그널: {','.join(s['signals'])}\n")
                f.write(f"   T: {s['title']}\n")
                f.write(f"   C: {s['content_head']}\n")
                f.write(f"   탈락사유: {s['reason']}\n")
                f.write(f"   matched: actions={s['matched_actions']}, targets={s['matched_targets']}, money={s['matched_money']}, area={s['matched_area']}\n")
                f.write(f"   excludes: {s['matched_excludes']}\n\n")
            if len(group) > 50:
                f.write(f"... +{len(group)-50}건 더 있음\n")

    print(f"\n💾 저장: {out_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
