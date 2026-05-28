"""90일치 RSS 필터링 검증 — stage1 통과 후 HIGH/MID 를 Claude(Haiku)로 재분류.

목적: 사용자가 본 RSS HIGH 4건 중 4건 모두 false positive(고용위기/특허소송/AX점검/준공이전).
stage1 룰이 패턴 매칭만 보고 의미를 안 봐서. AI로 의미 검증하면 어떤 false positive 패턴이 나오는지 추출.

입력: data/cache/test_3months/rss_filtered.jsonl (90일치 stage1 통과 128건)
출력: data/cache/test_3months/rss_ai_audit.jsonl + 콘솔 통계

각 article 에 대해 Claude haiku에게 묻는 질문:
  "이 기사가 시공사(자이씨앤에이, 건설업체) 영업 대상인가? Y/N + 이유 한 줄"

판단 기준:
  Y = 공장·연구소·물류센터 등 신규 시공/증설/리뉴얼 발표
  N = 정책/소송/인사/금융/AI 도입/이미 준공 등

사용:
  python scripts/audit_rss_with_ai.py
  python scripts/audit_rss_with_ai.py --tier HIGH    # HIGH 만
  python scripts/audit_rss_with_ai.py --limit 20     # 처음 20건만 (테스트)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from src.common.llm_client import MODEL_HAIKU, call_llm_json  # noqa: E402

# daily_report_html 안의 classify_rss 를 그대로 import (HIGH/MID/LOW 분류 재현)
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from daily_report_html import classify_rss  # noqa: E402


SYSTEM_PROMPT = """너는 건설 시공사(자이씨앤에이) 영업팀의 뉴스 필터링 보조자다.
RSS 뉴스 1건을 보고 "이게 진짜 시공사 영업 대상인가" 판단한다.

✅ 영업 대상 (Y):
- 공장·연구소·물류센터·데이터센터·캠퍼스의 신규 건설/증설/착공 발표
- 부지 매입 + 시설투자 계획
- 시공사 미정 단계의 시설 투자 결정

❌ 영업 대상 아님 (N):
- 고용·노동·산업정책 (지원금·고용유지·산업단지 지정 등)
- 법률 분쟁 (특허·소송·평결·법원)
- AX·AI·DX 등 기술 투자만, 시설 신축 아님
- 자동화로 공장 면적이 오히려 줄어드는 케이스
- 이미 준공 완료 + 이전·운영 시작 발표
- 시공사 이미 결정된 후의 후속 보도
- 인사·조직개편·임원 변경
- 지주회사·M&A·자회사 설립·상장
- 매장·점포·체험관 오픈 (소규모)
- 정부 정책 발표 (산업 카테고리만 잡혔을 뿐)
- 주가·실적·증권가 분석
- 사고·재해·안전 이슈

반드시 JSON 으로만 답:
{
  "verdict": "Y" | "N",
  "category": "신규시설"|"증설"|"부지매입"|"정책"|"소송"|"인사"|"준공후"|"AX기술"|"매장"|"실적"|"사고"|"기타",
  "reason": "한 문장 이내"
}"""


def audit_one(item: dict) -> dict:
    """1건 분류. 실패 시 None."""
    title = item.get("title", "")[:200]
    content = (item.get("content", "") or "")[:1500]
    user = f"제목: {title}\n\n본문: {content}"
    try:
        result = call_llm_json(MODEL_HAIKU, SYSTEM_PROMPT, user, max_tokens=200)
        return result
    except Exception as e:
        return {"verdict": "ERR", "category": "error", "reason": str(e)[:120]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/cache/test_3months/rss_filtered.jsonl")
    ap.add_argument("--output", default="data/cache/test_3months/rss_ai_audit.jsonl")
    ap.add_argument("--tier", choices=["HIGH", "MID", "LOW", "ALL"], default="HIGH,MID",
                    help="기본: HIGH+MID (LOW 는 어차피 노이즈 가능성 큼)")
    ap.add_argument("--limit", type=int, default=0, help="0 = 전체")
    args = ap.parse_args()

    in_path = PROJECT_ROOT / args.input
    out_path = PROJECT_ROOT / args.output
    target_tiers = set(args.tier.split(","))
    if "ALL" in target_tiers:
        target_tiers = {"HIGH", "MID", "LOW"}

    items = []
    with in_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    print(f"📥 입력: {len(items)}건 ({in_path.relative_to(PROJECT_ROOT)})")

    # stage1 결과 재현
    classified = []
    for it in items:
        label, reason = classify_rss(it)
        if label in target_tiers:
            classified.append((label, reason, it))
    print(f"🎯 분석 대상: {len(classified)}건 (tier={','.join(sorted(target_tiers))})")
    if args.limit:
        classified = classified[: args.limit]
        print(f"   ↳ --limit {args.limit} → {len(classified)}건만")

    # AI 분류
    results = []
    t_start = time.time()
    for i, (label, stage1_reason, it) in enumerate(classified, 1):
        title = it.get("title", "")[:60]
        ai = audit_one(it)
        verdict = ai.get("verdict", "?")
        cat = ai.get("category", "")
        reason = ai.get("reason", "")
        flag = "✅" if verdict == "Y" else ("❌" if verdict == "N" else "⚠️")
        print(f"  [{i:3d}/{len(classified)}] {label:4} {flag} {cat:8} | {title}")
        if verdict == "N":
            print(f"       ↳ {reason}")
        results.append({
            "id": it.get("id"),
            "stage1_tier": label,
            "stage1_reason": stage1_reason,
            "stage1_patterns": it.get("stage1_matched_patterns", []),
            "title": it.get("title"),
            "url": it.get("url"),
            "ai_verdict": verdict,
            "ai_category": cat,
            "ai_reason": reason,
        })
    dt = time.time() - t_start
    print(f"\n⏱  AI 분류 완료: {dt:.0f}초 ({dt/max(1,len(classified)):.1f}초/건)")

    # 저장
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"💾 저장: {out_path.relative_to(PROJECT_ROOT)}")

    # 통계
    print("\n" + "=" * 60)
    print("📊 분류 결과 (stage1 vs AI)")
    print("=" * 60)
    by_tier = Counter()
    by_tier_y = Counter()
    by_cat = Counter()
    by_cat_per_tier: dict[str, Counter] = {}
    for r in results:
        tier = r["stage1_tier"]
        verdict = r["ai_verdict"]
        cat = r["ai_category"]
        by_tier[tier] += 1
        if verdict == "Y":
            by_tier_y[tier] += 1
        if verdict == "N":
            by_cat[cat] += 1
        by_cat_per_tier.setdefault(tier, Counter())[cat] += 1

    for tier in ("HIGH", "MID", "LOW"):
        total = by_tier[tier]
        if not total:
            continue
        y = by_tier_y[tier]
        n = total - y
        precision = y / total * 100
        print(f"  {tier:4} : {total:3d}건  → Y {y:3d}건 / N {n:3d}건  (precision {precision:.0f}%)")

    print("\n📂 N (영업 무관) 의 false positive 카테고리 Top:")
    for cat, n in by_cat.most_common(10):
        print(f"   {cat:8} : {n:3d}건")

    print("\n📋 각 tier 내 false positive 카테고리 분포:")
    for tier in ("HIGH", "MID", "LOW"):
        counter = by_cat_per_tier.get(tier)
        if not counter:
            continue
        print(f"  [{tier}]")
        for cat, n in counter.most_common(8):
            print(f"     {cat:8} : {n:3d}건")


if __name__ == "__main__":
    main()
