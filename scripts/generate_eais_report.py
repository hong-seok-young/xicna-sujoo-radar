"""EAIS Stage 0 시뮬레이션 → HTML 리포트.

현재 캐시(`data/cache/eais/*.json`) 안의 인허가를 지금까지 손본 필터
(용도 + 동 클러스터 시그널 + 학교 컷 + 추정공사비)로 거른 결과를 보여준다.

출력: data/reports/eais_simulation_{YYYY-MM-DD}.html
"""
from __future__ import annotations

import csv
import html
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.stage0_collect._eais_cost import (
    COST_BY_CATEGORY,
    estimate_cost_man,
    format_cost,
)
from src.stage0_collect.eais import _is_target_purpose

CACHE_DIR = Path("data/cache/eais")
INDUSTRIAL_DONG_CSV = Path("config/industrial_dongs.csv")
OUT_DIR = Path("data/reports")
THRESHOLD_MAN = 450 * 10000   # 450억
KST = "+09:00"


# ─────────────────────────────────────────────────────────
# 1. 로드
# ─────────────────────────────────────────────────────────


def load_dong_categories() -> dict[str, list[str]]:
    if not INDUSTRIAL_DONG_CSV.exists():
        return {}
    out: dict[str, list[str]] = {}
    with INDUSTRIAL_DONG_CSV.open("r", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            key = f"{r['sigungu_cd']}-{r['bjdong_cd']}"
            out[key] = [c for c in (r.get("categories") or "").split("|") if c]
    return out


def load_cache_items() -> list[dict]:
    """각 캐시의 items를 location/dong_categories 와 함께 평탄화."""
    dong_cats = load_dong_categories()
    rows: list[dict] = []
    for p in sorted(CACHE_DIR.glob("*.json")):
        if p.name == "_index.json":
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        key = f"{d.get('sigungu_cd', '')}-{d.get('bjdong_cd', '')}"
        cats = dong_cats.get(key, [])
        loc = d.get("full_nm", key)
        fetched_at = d.get("fetched_at", "")
        for it in d.get("items", []):
            rows.append({
                "location": loc,
                "key": key,
                "dong_categories": cats,
                "fetched_at": fetched_at,
                "item": it,
            })
    return rows


# ─────────────────────────────────────────────────────────
# 2. 필터링 — 1단계(산업타겟) + 2단계(추정공사비)
# ─────────────────────────────────────────────────────────


def classify_row(row: dict) -> dict:
    it = row["item"]
    purpose = (it.get("mainPurpsCdNm") or "").strip()
    bldnm = (it.get("bldNm") or "").strip()
    plat = (it.get("platPlc") or "").strip()
    area_raw = it.get("totArea")
    is_target = _is_target_purpose(purpose)
    cost_man, category, area = estimate_cost_man(
        purpose, area_raw, bldnm, plat,
        dong_categories=row["dong_categories"] or None,
    )
    return {
        **row,
        "purpose": purpose,
        "bldnm": bldnm,
        "platPlc": plat,
        "area": area or 0,
        "is_target": is_target,
        "cost_man": cost_man,
        "category": category,
        "arch_day": (it.get("archPmsDay") or "").strip(),
        "mgm_pk": (it.get("mgmPkValue") or "").strip(),
    }


# ─────────────────────────────────────────────────────────
# 3. 중복 제거 — 같은 platPlc + 같은 area + 같은 purpose 면 1개
# ─────────────────────────────────────────────────────────


def dedup_passers(passers: list[dict]) -> tuple[list[dict], list[dict]]:
    """450억+ 통과 항목에서 중복 묶기.

    Returns:
        (대표 통과 항목 리스트, 묶인 중복 그룹 메타)
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for p in passers:
        # 같은 부지 + 같은 면적 + 같은 용도 → 같은 건축물의 중복 레코드로 간주
        groupkey = (p["platPlc"], round(p["area"], 1), p["purpose"])
        groups[groupkey].append(p)
    dedup: list[dict] = []
    duplicates_meta: list[dict] = []
    for key, items in groups.items():
        # 가장 최근 archPmsDay 를 대표로
        items.sort(key=lambda x: x["arch_day"], reverse=True)
        rep = items[0]
        rep = {**rep, "dup_count": len(items)}
        dedup.append(rep)
        if len(items) > 1:
            duplicates_meta.append({
                "platPlc": key[0], "purpose": key[2],
                "count": len(items),
            })
    dedup.sort(key=lambda x: -x["cost_man"])
    return dedup, duplicates_meta


# ─────────────────────────────────────────────────────────
# 4. HTML 렌더
# ─────────────────────────────────────────────────────────


CSS = """
* { box-sizing: border-box; }
body {
  font-family: -apple-system, "Segoe UI", "Malgun Gothic", sans-serif;
  margin: 0; padding: 32px;
  background: #f7f7f5; color: #222;
}
.container { max-width: 1180px; margin: 0 auto; }
h1 { margin: 0 0 6px; font-size: 26px; }
.subtitle { color: #666; font-size: 14px; margin-bottom: 28px; }
section { background: white; border-radius: 8px; padding: 24px; margin-bottom: 20px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
section h2 { margin: 0 0 14px; font-size: 18px; border-left: 3px solid #2563eb; padding-left: 10px; }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.stat { background: #f5f7fb; border-radius: 6px; padding: 14px 16px; }
.stat-label { font-size: 12px; color: #666; margin-bottom: 4px; }
.stat-value { font-size: 22px; font-weight: 600; color: #111; }
.stat-value.warn { color: #d97706; }
.stat-value.bad { color: #dc2626; }
.stat-value.ok { color: #059669; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 10px; text-align: left; border-bottom: 1px solid #e5e7eb; }
th { background: #f3f4f6; font-weight: 600; color: #374151; }
tr:hover td { background: #fafbff; }
.cost { font-variant-numeric: tabular-nums; font-weight: 600; color: #2563eb; }
.cost.high { color: #dc2626; }
.cat-CR    { background: #fef3c7; color: #92400e; padding: 2px 8px; border-radius: 4px; font-size: 11px; }
.cat-이차전지 { background: #dbeafe; color: #1e40af; padding: 2px 8px; border-radius: 4px; font-size: 11px; }
.cat-제약     { background: #d1fae5; color: #065f46; padding: 2px 8px; border-radius: 4px; font-size: 11px; }
.cat-RD     { background: #ede9fe; color: #5b21b6; padding: 2px 8px; border-radius: 4px; font-size: 11px; }
.cat-식품     { background: #fed7aa; color: #9a3412; padding: 2px 8px; border-radius: 4px; font-size: 11px; }
.cat-일반     { background: #e5e7eb; color: #374151; padding: 2px 8px; border-radius: 4px; font-size: 11px; }
.cat-발전     { background: #fee2e2; color: #991b1b; padding: 2px 8px; border-radius: 4px; font-size: 11px; }
.cat-기타     { background: #f3f4f6; color: #6b7280; padding: 2px 8px; border-radius: 4px; font-size: 11px; }
.bar { height: 18px; background: #2563eb; border-radius: 2px; min-width: 2px; }
.bar-cell { width: 220px; }
.dim { color: #999; font-size: 11px; }
.muted { color: #666; }
.warn-box { background: #fffbea; border-left: 3px solid #d97706; padding: 12px 16px; border-radius: 4px; font-size: 13px; }
.empty { padding: 24px; text-align: center; color: #999; font-size: 13px; }
ul.findings { padding-left: 22px; line-height: 1.7; }
ul.findings li { margin-bottom: 4px; }
.kbd { background: #f3f4f6; padding: 1px 5px; border-radius: 3px; font-family: monospace; font-size: 12px; }
"""


def cat_class(cat: str) -> str:
    m = {
        "CR": "cat-CR",
        "이차전지": "cat-이차전지",
        "제약/바이오": "cat-제약",
        "R&D": "cat-RD",
        "식품/음료": "cat-식품",
        "일반생산": "cat-일반",
        "발전/위험물": "cat-발전",
        "기타": "cat-기타",
    }
    return m.get(cat, "cat-기타")


def render_row(r: dict) -> str:
    arch = r["arch_day"]
    arch_fmt = f"{arch[:4]}-{arch[4:6]}-{arch[6:8]}" if len(arch) >= 8 else arch or "—"
    cost_class = "cost high" if r["cost_man"] >= 4500000 else "cost"
    dup = f" <span class='dim'>×{r['dup_count']}</span>" if r.get("dup_count", 1) > 1 else ""
    return f"""
    <tr>
      <td>{arch_fmt}</td>
      <td>{html.escape(r['location'])}</td>
      <td>{html.escape(r['purpose'])}{dup}</td>
      <td>{html.escape(r['bldnm'] or '—')}</td>
      <td style="text-align: right;">{r['area']:,.0f}㎡</td>
      <td><span class="{cat_class(r['category'])}">{html.escape(r['category'])}</span></td>
      <td class="{cost_class}" style="text-align: right;">{format_cost(r['cost_man'])}</td>
    </tr>
    """


def render_thresholds(industrial: list[dict], costables: list[dict], total: int) -> str:
    thresholds = [50, 100, 150, 200, 300, 450, 600, 1000]
    max_n = len(industrial) or 1
    rows = []
    for eok in thresholds:
        n = sum(1 for c in costables if c["cost_man"] >= eok * 10000)
        pct = 100 * n / total if total else 0
        bar_w = int(220 * n / max_n)
        rows.append(f"""
          <tr>
            <td><strong>{eok:,}억+</strong></td>
            <td style="text-align: right;">{n}건</td>
            <td>{pct:.1f}%</td>
            <td class="bar-cell"><div class="bar" style="width:{bar_w}px;"></div></td>
          </tr>
        """)
    return "\n".join(rows)


def render_by_dong(industrial: list[dict]) -> str:
    by = Counter(r["location"] for r in industrial)
    rows = []
    for loc, n in by.most_common():
        rows.append(f"<tr><td>{html.escape(loc)}</td><td style='text-align:right;'>{n}건</td></tr>")
    return "\n".join(rows) or "<tr><td colspan='2' class='empty'>—</td></tr>"


def render_by_category(industrial: list[dict]) -> str:
    by = Counter(r["category"] for r in industrial)
    rows = []
    for cat, n in by.most_common():
        unit = COST_BY_CATEGORY.get(cat, 100)
        rows.append(f"""
          <tr>
            <td><span class="{cat_class(cat)}">{html.escape(cat)}</span></td>
            <td style="text-align: right;">{n}건</td>
            <td style="text-align: right;">{unit:,}만원/㎡</td>
          </tr>
        """)
    return "\n".join(rows) or "<tr><td colspan='3' class='empty'>—</td></tr>"


def render_html(report_date: str,
                total_raw: int,
                dongs_cached: int,
                industrial: list[dict],
                costables: list[dict],
                passers: list[dict],
                duplicates_meta: list[dict]) -> str:
    n_total = total_raw
    n_ind = len(industrial)
    n_passers = len(passers)

    dup_note = ""
    if duplicates_meta:
        merged = sum(m["count"] - 1 for m in duplicates_meta)
        dup_note = f" <span class='dim'>(중복 {merged}건 묶음)</span>"

    findings_html = """
    <ul class="findings">
      <li><b>날짜 필터(7일)</b> — 코드에 적용 확인. CLI default <span class="kbd">--days 7</span>.</li>
      <li><b>동 클러스터 시그널</b> — <span class="kbd">industrial_dongs.csv</span> 의 <span class="kbd">categories</span> 컬럼이 단가 추정에 1차 신호로 사용. 단 <b>공장·창고·위험물·발전</b> 같은 산업 전용 용도일 때만 적용 — 근생·업무·교육연구는 키워드 매칭으로 회귀.</li>
      <li><b>학교/공공 false positive 컷</b> — 강남·서초 R&D 동 안의 학교·학원·아카데미·혁신파크·복지·문화센터는 R&D 단가가 아니라 "기타" 단가로 떨굼.</li>
      <li><b>시도 별칭·자치구 자동 정규화</b> — <span class="kbd">scripts/build_industrial_dongs.py</span> 가 "강원도→강원특별자치도", "용인시처인구→용인시" 자동 처리 + 미매칭 시 difflib fuzzy 제안.</li>
      <li><b>API 안전망</b> — <span class="kbd">DEFAULT_QUOTA=900</span> (1,000 한도 - 100 마진), <span class="kbd">picked[:quota]</span> 컷, 페이지당 150ms sleep, multi-page 동 최대 10페이지.</li>
      <li class="muted"><b>알려진 한계</b> — 자치구 미구분 (용인시→처인·기흥·수지·처인 전부 포함), bldNm에 인명 들어간 용도변경 케이스 식별 X, 동일 부지 중복 인허가는 사후 dedup으로 처리.</li>
    </ul>
    """

    if not passers:
        passer_table = "<div class='empty'>현재 캐시 + 필터 기준으로 450억+ 통과 항목이 없습니다.</div>"
    else:
        passer_rows = "\n".join(render_row(p) for p in passers)
        passer_table = f"""
        <table>
          <thead><tr>
            <th>인허가일</th><th>위치</th><th>용도</th><th>건물명</th>
            <th style="text-align:right;">연면적</th><th>카테고리</th>
            <th style="text-align:right;">추정공사비</th>
          </tr></thead>
          <tbody>{passer_rows}</tbody>
        </table>
        """

    # 의심 케이스: 450억+ 통과 중 bldNm 비었거나 인명 패턴
    suspicious = []
    for p in passers:
        b = p["bldnm"]
        reasons = []
        if not b or b == "—":
            reasons.append("건물명 비어있음 (확인 필요)")
        elif any(c.isalpha() or c in "()" for c in b):
            pass  # 영문/숫자 섞임은 정상
        # 한글 인명 패턴 (괄호로 둘러싸인 인명 등은 평소 bldNm에 안 들어감)
        if "(" in b and ")" in b and len(b) < 40:
            reasons.append("건물명에 괄호+이름 패턴 — 용도변경 가능성")
        if "프라자" in b or "빌딩" in b or "타워" in b:
            reasons.append("상업 빌딩 명칭 — 임대 영업가치 검토")
        if reasons:
            suspicious.append((p, reasons))

    if suspicious:
        susp_rows = []
        for p, reasons in suspicious:
            susp_rows.append(f"""
              <tr>
                <td>{html.escape(p['location'])}</td>
                <td>{html.escape(p['purpose'])}</td>
                <td>{html.escape(p['bldnm'] or '—')}</td>
                <td class="cost high" style="text-align:right;">{format_cost(p['cost_man'])}</td>
                <td class="muted">{' / '.join(reasons)}</td>
              </tr>
            """)
        suspicious_html = f"""
        <div class="warn-box">
          아래 항목들은 자동 필터를 통과했지만 사람이 한번 더 확인할 가치가 있는 케이스입니다.
          향후 Stage 2 (Haiku 분류) 에서 한 번 더 거릅니다.
        </div>
        <table style="margin-top:12px;">
          <thead><tr>
            <th>위치</th><th>용도</th><th>건물명</th>
            <th style="text-align:right;">추정공사비</th><th>의심 이유</th>
          </tr></thead>
          <tbody>{''.join(susp_rows)}</tbody>
        </table>
        """
    else:
        suspicious_html = "<div class='empty'>의심 케이스 없음.</div>"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>EAIS Stage 0 시뮬레이션 — {report_date}</title>
  <style>{CSS}</style>
</head>
<body>
<div class="container">

  <h1>EAIS Stage 0 시뮬레이션 리포트</h1>
  <div class="subtitle">
    수주레이더 · 자이씨앤에이 영업팀 · 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}
    · 임계값 <b>450억</b> · 캐시 {dongs_cached}개 동 / {total_raw}건
  </div>

  <section>
    <h2>요약</h2>
    <div class="stats">
      <div class="stat"><div class="stat-label">전체 raw 인허가</div><div class="stat-value">{n_total:,}건</div></div>
      <div class="stat"><div class="stat-label">산업타겟 용도 통과</div><div class="stat-value ok">{n_ind:,}건</div></div>
      <div class="stat"><div class="stat-label">공사비 추정 가능</div><div class="stat-value">{len(costables):,}건</div></div>
      <div class="stat"><div class="stat-label">450억+ 통과</div><div class="stat-value warn">{n_passers}건{dup_note}</div></div>
    </div>
  </section>

  <section>
    <h2>450억+ 통과 인허가</h2>
    {passer_table}
  </section>

  <section>
    <h2>의심 케이스 — 사람 확인 필요</h2>
    {suspicious_html}
  </section>

  <section>
    <h2>임계값별 통과 분포</h2>
    <table>
      <thead><tr>
        <th>임계값</th><th style="text-align: right;">통과</th><th>raw 대비</th><th>분포</th>
      </tr></thead>
      <tbody>{render_thresholds(industrial, costables, n_total)}</tbody>
    </table>
  </section>

  <section style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px;">
    <div>
      <h2>동별 산업타겟 분포</h2>
      <table>
        <thead><tr><th>위치</th><th style="text-align: right;">통과 건수</th></tr></thead>
        <tbody>{render_by_dong(industrial)}</tbody>
      </table>
    </div>
    <div>
      <h2>카테고리별 분포</h2>
      <table>
        <thead><tr><th>카테고리</th><th style="text-align: right;">건수</th><th style="text-align: right;">단가</th></tr></thead>
        <tbody>{render_by_category(industrial)}</tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>지금까지 적용된 필터·안전망 (코드에 박힌 것)</h2>
    {findings_html}
  </section>

  <section>
    <h2>현재 캐시의 한계</h2>
    <div class="warn-box">
      이 리포트는 <b>현재 캐시(<span class="kbd">data/cache/eais/*.json</span>)</b>에 들어있는 인허가만으로 시뮬레이션한 결과입니다.
      캐시는 <b>{dongs_cached}개 동</b> 분량(<b>{total_raw}건</b>)이라 영업 인사이트로 쓰기엔 작습니다.
      <br>
      실제 운영 시 <span class="kbd">--days 7 --quota 900</span> 으로 매일 돌리면 1,152개 산업 후보 동을 며칠 안에 다 깨물고 7일 윈도우 안의 신규 인허가가 누적됩니다.
    </div>
  </section>

</div>
</body>
</html>
"""


# ─────────────────────────────────────────────────────────
# 5. main
# ─────────────────────────────────────────────────────────


def main():
    rows = load_cache_items()
    if not rows:
        print("⚠️ 캐시가 비어있습니다. data/cache/eais/ 안에 *.json 이 있는지 확인하세요.")
        sys.exit(1)

    dongs_cached = len({r["key"] for r in rows})
    total_raw = len(rows)

    enriched = [classify_row(r) for r in rows]
    industrial = [r for r in enriched if r["is_target"]]
    costables = [r for r in industrial if r["area"] > 0]
    passers_raw = [r for r in costables if r["cost_man"] >= THRESHOLD_MAN]
    passers, duplicates_meta = dedup_passers(passers_raw)

    print(f"전체 raw: {total_raw}건 ({dongs_cached}개 동)")
    print(f"  → 산업타겟: {len(industrial)}건")
    print(f"  → 공사비 추정 가능: {len(costables)}건")
    print(f"  → 450억+ 통과: {len(passers_raw)}건 → 중복제거 후 {len(passers)}건")

    report_date = datetime.now().strftime("%Y-%m-%d")
    html_doc = render_html(report_date, total_raw, dongs_cached,
                            industrial, costables, passers, duplicates_meta)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"eais_simulation_{report_date}.html"
    out_path.write_text(html_doc, encoding="utf-8")

    print()
    print(f"✓ HTML 리포트 생성: {out_path.resolve()}")
    print(f"   브라우저에서 열기: file:///{str(out_path.resolve()).replace(chr(92), '/')}")


if __name__ == "__main__":
    main()
