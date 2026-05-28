"""Stage 1 통과한 기사를 사람이 검토하기 좋게 분류·정리한 markdown 리포트 생성.

휴리스틱 기준:
- 🟢 HIGH (수주 가능성 강함)
    매칭에 강한 action(착공/기공/준공/신축/증설/신설/발주/수주/낙찰/완공/기공식) +
    강한 target(공장/플랜트/캠퍼스/단지/라인/CR/GMP/센터/시설)
    + money 또는 area 가 같이 있을 때
- 🟡 MID (수주 가능성 중간)
    위 조건 일부만 만족 (예: action+target 있지만 money/area 없음)
- 🔴 LOW (노이즈일 확률 높음)
    weak action (체결/추진/검토/획득/지정)만 있거나
    부동산·증권 키워드(매수·주가·지주회사·상장·인수·합병·아파트·매각) 매칭

사용법:
    python scripts/review_filtered.py
    python scripts/review_filtered.py --input data/filtered/2026-05-20.jsonl
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))

STRONG_ACTIONS = {"착공", "기공", "준공", "신축", "증설", "신설", "발주", "수주",
                  "낙찰", "완공", "기공식", "개소", "확충", "확장"}
WEAK_ACTIONS = {"체결", "추진", "검토", "획득", "지정", "선정", "취득", "양수", "투자"}
STRONG_TARGETS = {"공장", "플랜트", "캠퍼스", "단지", "라인", "CR", "클린룸",
                  "GMP", "센터", "시설", "기가팩토리", "데이터센터", "물류센터"}
NOISE_KEYWORDS = ["주가", "목표가", "지주회사", "상장", "인수합병", "아파트", "매각",
                  "유상증자", "공모주", "코스피", "코스닥", "환율", "유가",
                  "재건축", "재개발", "분양", "청약", "치료센터", "병원"]


def classify(item: dict) -> tuple[str, str]:
    """returns (label, reason). label ∈ {🟢HIGH, 🟡MID, 🔴LOW}."""
    patterns = item.get("stage1_matched_patterns", [])
    title = item.get("title", "")
    content = item.get("content", "")
    text = f"{title} {content}"

    has_strong_action = False
    has_weak_only_action = False
    has_strong_target = False
    has_money = False
    has_area = False

    for p in patterns:
        if p.startswith("action:"):
            actions = p[len("action:"):].split(",")
            if any(a in STRONG_ACTIONS for a in actions):
                has_strong_action = True
            elif any(a in WEAK_ACTIONS for a in actions):
                has_weak_only_action = True
        elif p.startswith("target:"):
            targets = p[len("target:"):].split(",")
            if any(t in STRONG_TARGETS for t in targets):
                has_strong_target = True
        elif p.startswith("money:"):
            has_money = True
        elif p.startswith("area:"):
            has_area = True

    # 노이즈 키워드 매칭
    noise_hits = [k for k in NOISE_KEYWORDS if k in text]
    is_noisy = len(noise_hits) >= 2 or any(k in title for k in NOISE_KEYWORDS)

    # 분류
    if has_strong_action and has_strong_target and (has_money or has_area) and not is_noisy:
        return "🟢 HIGH", "강한 action+target+규모"
    if has_strong_action and has_strong_target and not is_noisy:
        return "🟢 HIGH", "강한 action+target"
    if has_strong_action and (has_money or has_area):
        return "🟡 MID", "강한 action+규모 (target 약함)"
    if has_strong_target and (has_money or has_area):
        return "🟡 MID", "강한 target+규모 (action 약함)"
    if is_noisy:
        return "🔴 LOW", f"노이즈 키워드: {noise_hits[:3]}"
    if has_weak_only_action and not has_strong_target:
        return "🔴 LOW", "weak action만, target 약함"
    return "🟡 MID", "기타"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/filtered/2026-05-20.jsonl")
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    input_path = Path(args.input)
    items = []
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))

    classified = []
    for it in items:
        label, reason = classify(it)
        classified.append((label, reason, it))

    # 통계
    label_counts = Counter(c[0] for c in classified)
    src_counts = Counter(it["source"] for _, _, it in classified)
    src_by_label = defaultdict(Counter)
    for label, _, it in classified:
        src_by_label[label][it["source"]] += 1

    if args.output:
        out = Path(args.output)
    else:
        date = datetime.now(KST).strftime("%Y-%m-%d")
        out = Path(f"data/review_{date}.md")

    lines: list[str] = []
    lines.append(f"# Stage 1 통과 기사 검토 리포트")
    lines.append("")
    lines.append(f"- 입력: `{input_path}`")
    lines.append(f"- 총 {len(items)}건")
    lines.append(f"- 🟢 HIGH: {label_counts.get('🟢 HIGH', 0)}건  /  🟡 MID: {label_counts.get('🟡 MID', 0)}건  /  🔴 LOW: {label_counts.get('🔴 LOW', 0)}건")
    lines.append("")
    lines.append("## 매체별 분포")
    lines.append("")
    lines.append("| 매체 | 총 | 🟢 HIGH | 🟡 MID | 🔴 LOW |")
    lines.append("|------|----|---------|---------|---------|")
    for src, total in src_counts.most_common():
        high = src_by_label["🟢 HIGH"][src]
        mid = src_by_label["🟡 MID"][src]
        low = src_by_label["🔴 LOW"][src]
        lines.append(f"| {src} | {total} | {high} | {mid} | {low} |")
    lines.append("")

    # 🟢 HIGH 전체
    high_items = [(r, it) for l, r, it in classified if l == "🟢 HIGH"]
    lines.append(f"## 🟢 HIGH — 강한 수주 시그널 ({len(high_items)}건)")
    lines.append("")
    lines.append("> 산단 조성, 공장 신증설, 플랜트 발주 같은 명확한 케이스. **이게 진짜 알짜.**")
    lines.append("")
    for i, (reason, it) in enumerate(high_items, 1):
        lines.append(f"### {i}. [{it['source']}] {it['title']}")
        lines.append(f"- **분류 사유**: {reason}")
        lines.append(f"- **매칭 패턴**: `{it['stage1_matched_patterns']}`")
        lines.append(f"- **URL**: {it['url']}")
        if it.get("content"):
            preview = it["content"][:200].replace("\n", " ")
            lines.append(f"- **본문 미리보기**: {preview}...")
        lines.append("")

    # 🟡 MID 샘플 30건
    mid_items = [(r, it) for l, r, it in classified if l == "🟡 MID"]
    lines.append(f"## 🟡 MID — 애매 ({len(mid_items)}건, 상위 30건 표시)")
    lines.append("")
    lines.append("> 일부 조건만 만족. **사람이 한 번 더 봐야 할 영역. Haiku 분류기가 진가 발휘할 곳.**")
    lines.append("")
    lines.append("| # | 매체 | 제목 | 매칭 |")
    lines.append("|---|------|------|------|")
    for i, (reason, it) in enumerate(mid_items[:30], 1):
        patterns_str = ", ".join(it["stage1_matched_patterns"][:3])
        lines.append(f"| {i} | {it['source']} | {it['title'][:60]} | {patterns_str} |")
    lines.append("")

    # 🔴 LOW 상위 20건 (왜 노이즈로 판단됐는지)
    low_items = [(r, it) for l, r, it in classified if l == "🔴 LOW"]
    lines.append(f"## 🔴 LOW — 노이즈 가능성 높음 ({len(low_items)}건, 상위 20건 표시)")
    lines.append("")
    lines.append("> 룰만으로는 못 거른 케이스. **Haiku 분류기가 처리해야 할 노이즈.**")
    lines.append("")
    lines.append("| # | 매체 | 제목 | 노이즈 사유 |")
    lines.append("|---|------|------|------------|")
    for i, (reason, it) in enumerate(low_items[:20], 1):
        lines.append(f"| {i} | {it['source']} | {it['title'][:60]} | {reason} |")
    lines.append("")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"리포트 저장: {out}")
    print(f"  🟢 HIGH {label_counts.get('🟢 HIGH', 0)}  /  🟡 MID {label_counts.get('🟡 MID', 0)}  /  🔴 LOW {label_counts.get('🔴 LOW', 0)}")


if __name__ == "__main__":
    main()
