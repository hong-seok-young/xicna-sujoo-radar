"""자이씨앤에이 수주레이더 — 일일 통합 검토 리포트.

3개 소스(RSS / 나라장터 G2B / DART)를 1장의 마크다운으로 합쳐서
사람이 빠르게 훑어볼 수 있게 만든다.

알파 등급:
- ⭐⭐⭐ G2B / DART  → Stage 0 에서 이미 강필터 통과. 발주처 직접 신호.
- 🟢 RSS HIGH       → 강한 action + 강한 target + (규모 or 노이즈 없음)
- 🟡 RSS MID        → 일부 조건만 만족
- 🔴 RSS LOW        → 노이즈 가능성 (제목만 보고 빠르게 패스)

사용법:
    python scripts/daily_report.py
    python scripts/daily_report.py --rss data/filtered/2026-05-20.jsonl \\
                                  --g2b data/raw/g2b_2026-05-20.jsonl \\
                                  --dart data/raw/dart_2026-05-21.jsonl
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


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def classify_rss(item: dict) -> tuple[str, str]:
    """RSS 기사 휴리스틱 분류."""
    patterns = item.get("stage1_matched_patterns", []) or []
    title = item.get("title", "")
    content = item.get("content", "") or ""
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

    noise_hits = [k for k in NOISE_KEYWORDS if k in text]
    is_noisy = len(noise_hits) >= 2 or any(k in title for k in NOISE_KEYWORDS)

    if has_strong_action and has_strong_target and (has_money or has_area) and not is_noisy:
        return "HIGH", "강한 action+target+규모"
    if has_strong_action and has_strong_target and not is_noisy:
        return "HIGH", "강한 action+target"
    if has_strong_action and (has_money or has_area):
        return "MID", "강한 action+규모 (target 약함)"
    if has_strong_target and (has_money or has_area):
        return "MID", "강한 target+규모 (action 약함)"
    if is_noisy:
        return "LOW", f"노이즈 키워드: {noise_hits[:3]}"
    if has_weak_only_action and not has_strong_target:
        return "LOW", "weak action만, target 약함"
    return "MID", "기타"


def _preview(text: str, n: int = 180) -> str:
    if not text:
        return ""
    s = text.replace("\n", " ").replace("\r", " ").strip()
    return s[:n] + ("..." if len(s) > n else "")


def _money_in_title(s: str) -> str:
    """제목/내용에서 금액 한 조각만 뽑아 강조용 — 휴리스틱."""
    import re
    m = re.search(r"\d[\d,]*\s*(?:백만원|억원|조원|만원|원)", s)
    return m.group(0) if m else ""


def main():
    ap = argparse.ArgumentParser()
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    ap.add_argument("--rss", default=f"data/filtered/2026-05-20.jsonl")
    ap.add_argument("--g2b", default=f"data/raw/g2b_2026-05-20.jsonl")
    ap.add_argument("--dart", default=f"data/raw/dart_{today_str}.jsonl")
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    rss_path = Path(args.rss)
    g2b_path = Path(args.g2b)
    dart_path = Path(args.dart)

    rss_items = _read_jsonl(rss_path)
    g2b_items = _read_jsonl(g2b_path)
    dart_items = _read_jsonl(dart_path)

    # DART 를 1차 신호 vs 2차 정보 로 분리
    # 1차 = 발주처 본인 시설투자 결정 (영업 골든타임)
    # 2차 = 시공사·장비사 공급계약체결 (경쟁사·시장 정보)
    def _is_primary_signal(it: dict) -> bool:
        t = it.get("title", "").replace("ㆍ", "").replace("·", "").replace(" ", "")
        # 공급계약 들어가면 2차
        if "공급계약" in t:
            return False
        return True
    dart_primary = [it for it in dart_items if _is_primary_signal(it)]
    dart_secondary = [it for it in dart_items if not _is_primary_signal(it)]

    # RSS 분류
    rss_classified = [(classify_rss(it)[0], classify_rss(it)[1], it) for it in rss_items]
    rss_high = [(r, it) for l, r, it in rss_classified if l == "HIGH"]
    rss_mid = [(r, it) for l, r, it in rss_classified if l == "MID"]
    rss_low = [(r, it) for l, r, it in rss_classified if l == "LOW"]

    # 매체별 RSS 분포
    rss_src = Counter(it["source"] for it in rss_items)
    rss_src_by_label = defaultdict(Counter)
    for label, _, it in rss_classified:
        rss_src_by_label[label][it["source"]] += 1

    total = len(rss_items) + len(g2b_items) + len(dart_items)

    if args.output:
        out = Path(args.output)
    else:
        out = Path(f"data/daily_report_{today_str}.md")

    L: list[str] = []
    L.append(f"# 자이씨앤에이 수주레이더 — {today_str}")
    L.append("")
    L.append(f"**총 {total}건 알파** (G2B {len(g2b_items)} / DART {len(dart_items)} / RSS {len(rss_items)})")
    L.append("")
    L.append("| 우선순위 | 소스 | 건수 | 비고 |")
    L.append("|---------|------|------|------|")
    L.append(f"| ⭐⭐⭐ | 나라장터 G2B 시설공사 입찰공고 | {len(g2b_items)} | 발주처 본인 입찰 — 100% 알짜 |")
    L.append(f"| ⭐⭐⭐ | DART **1차 신호** (시설투자 결정) | {len(dart_primary)} | 시공사 미정 — 영업 골든타임 |")
    L.append(f"| ⭐⭐ | DART **2차 정보** (공급계약체결) | {len(dart_secondary)} | 시공사 결정됨 — 협력사·경쟁사 동향 |")
    L.append(f"| 🟢 | RSS HIGH | {len(rss_high)} | 강한 action+target+규모 |")
    L.append(f"| 🟡 | RSS MID | {len(rss_mid)} | 일부 조건만 만족 |")
    L.append(f"| 🔴 | RSS LOW | {len(rss_low)} | 노이즈 가능성 (요약만 표시) |")
    L.append("")
    L.append("> 💡 영업 우선순위: **G2B → DART 1차 → RSS HIGH → DART 2차 → RSS MID**.")
    L.append("> RSS LOW 는 시간 남을 때만 훑기.")
    L.append("")
    L.append("---")
    L.append("")

    # === 1. G2B ===
    L.append(f"## 1. ⭐⭐⭐ 나라장터 G2B 입찰공고 ({len(g2b_items)}건)")
    L.append("")
    L.append("> **발주처가 직접 올린 시설공사 입찰**. 이미 시공사 선정 절차 진행 중이지만,")
    L.append("> 입찰 참여 가능한 건이라 가장 직접적인 영업 기회.")
    L.append("")
    for i, it in enumerate(g2b_items, 1):
        L.append(f"### G2B-{i}. {it['title']}")
        published = it.get("published_at", "")[:10] if it.get("published_at") else ""
        L.append(f"- **공고일**: {published}")
        money = _money_in_title(it.get("content", ""))
        if money:
            L.append(f"- **추정가**: {money}")
        L.append(f"- **본문**: {_preview(it.get('content', ''), 220)}")
        L.append(f"- **URL**: {it['url']}")
        L.append("")
    L.append("---")
    L.append("")

    # === 2A. DART 1차 신호 ===
    L.append(f"## 2A. ⭐⭐⭐ DART 1차 신호 — 시설투자 결정 ({len(dart_primary)}건)")
    L.append("")
    L.append("> **발주처가 \"시설 짓겠다\" 결정한 공시**. 시공사 미정 — 공시 후 3~6개월 내")
    L.append("> 발주 진행. 영업 골든타임.")
    L.append("")
    for i, it in enumerate(dart_primary, 1):
        L.append(f"### DART1-{i}. {it['title']}")
        published = it.get("published_at", "")[:10] if it.get("published_at") else ""
        L.append(f"- **공시일**: {published}")
        L.append(f"- **본문**: {_preview(it.get('content', ''), 220)}")
        L.append(f"- **URL**: {it['url']}")
        L.append("")
    L.append("---")
    L.append("")

    # === 2B. DART 2차 정보 (테이블로 축약) ===
    L.append(f"## 2B. ⭐⭐ DART 2차 정보 — 시공사 공급계약체결 ({len(dart_secondary)}건)")
    L.append("")
    L.append("> 이미 시공사가 결정된 케이스. **협력사·하청 영업, 경쟁사 동향, 발주처-시공사")
    L.append("> 매칭 관계 파악용**. 표로 빠르게 훑고 관심 가는 건만 URL 클릭.")
    L.append("")
    L.append("| # | 회사 | 공시명 | 공시일 | URL |")
    L.append("|---|------|--------|--------|-----|")
    for i, it in enumerate(dart_secondary, 1):
        title = it["title"]
        # [회사명] 공시명 형태에서 분리
        if title.startswith("["):
            end = title.find("]")
            corp = title[1:end] if end > 0 else ""
            rpt = title[end+1:].strip() if end > 0 else title
        else:
            corp = ""
            rpt = title
        rpt_short = rpt[:50].replace("|", "/")
        published = it.get("published_at", "")[:10] if it.get("published_at") else ""
        L.append(f"| {i} | {corp} | {rpt_short} | {published} | [link]({it['url']}) |")
    L.append("")
    L.append("---")
    L.append("")

    # === 3. RSS HIGH ===
    L.append(f"## 3. 🟢 RSS 뉴스 HIGH ({len(rss_high)}건)")
    L.append("")
    L.append("> 강한 action(착공/신축/증설/수주) + 강한 target(공장/플랜트/클린룸 등).")
    L.append("> **2차 확인 필요**: 어느 단계인지 (계획/입찰/시공중/완공) 본문 봐야 함.")
    L.append("")
    for i, (reason, it) in enumerate(rss_high, 1):
        L.append(f"### RSS-H-{i}. [{it['source']}] {it['title']}")
        published = it.get("published_at", "")[:10] if it.get("published_at") else ""
        L.append(f"- **게시일**: {published}  |  **분류 사유**: {reason}")
        patterns = it.get("stage1_matched_patterns", [])
        L.append(f"- **매칭 패턴**: `{patterns}`")
        L.append(f"- **본문**: {_preview(it.get('content', ''), 220)}")
        L.append(f"- **URL**: {it['url']}")
        L.append("")
    L.append("---")
    L.append("")

    # === 4. RSS MID ===
    L.append(f"## 4. 🟡 RSS 뉴스 MID ({len(rss_mid)}건)")
    L.append("")
    L.append("> 일부 조건만 만족. **표 형태로 빠르게 훑기.**")
    L.append("> 제목만 보고 흥미로운 것만 클릭.")
    L.append("")
    L.append("| # | 매체 | 제목 | 매칭 | URL |")
    L.append("|---|------|------|------|-----|")
    for i, (reason, it) in enumerate(rss_mid, 1):
        title_short = it["title"][:70].replace("|", "/")
        patterns_str = ", ".join((it.get("stage1_matched_patterns") or [])[:2])
        patterns_str = patterns_str[:50]
        L.append(f"| {i} | {it['source']} | {title_short} | {patterns_str} | [link]({it['url']}) |")
    L.append("")
    L.append("---")
    L.append("")

    # === 5. RSS LOW (축약) ===
    L.append(f"## 5. 🔴 RSS 뉴스 LOW ({len(rss_low)}건)")
    L.append("")
    L.append("> 노이즈 가능성 큼. **제목만 줄로 표시 — 5초 안에 패스.**")
    L.append("")
    for i, (reason, it) in enumerate(rss_low, 1):
        title_short = it["title"][:90]
        L.append(f"- `[{it['source']}]` {title_short}  ← _{reason}_")
    L.append("")
    L.append("---")
    L.append("")

    # === 부록: 매체별 분포 ===
    L.append("## 부록. RSS 매체별 분포")
    L.append("")
    L.append("| 매체 | 총 | 🟢 HIGH | 🟡 MID | 🔴 LOW |")
    L.append("|------|----|---------|---------|---------|")
    for src, n in rss_src.most_common():
        h = rss_src_by_label["HIGH"][src]
        m = rss_src_by_label["MID"][src]
        lo = rss_src_by_label["LOW"][src]
        L.append(f"| {src} | {n} | {h} | {m} | {lo} |")
    L.append("")
    L.append(f"_생성: {datetime.now(KST).strftime('%Y-%m-%d %H:%M')} KST_")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"리포트 저장: {out}")
    print(f"  ⭐ G2B {len(g2b_items)}  ⭐ DART {len(dart_items)}  "
          f"🟢 RSS-HIGH {len(rss_high)}  🟡 RSS-MID {len(rss_mid)}  🔴 RSS-LOW {len(rss_low)}")


if __name__ == "__main__":
    main()
