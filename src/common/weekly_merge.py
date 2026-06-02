"""주간 취합 — 날짜별 JSONL 윈도우 병합 + ID 중복제거.

매일 수집해 쌓인 날짜별 RSS 파일(예: data/pool/rss_2026-06-02.jsonl)을 N일 윈도우로
합치고, id(URL 해시) 기준 중복을 제거한다.

배경: RSS 피드는 보통 최신 20~50건만 보관 → 주 1회만 수집하면 기사 많은 매체의
초반(월·화) 기사가 금요일엔 피드에서 밀려나 누락된다. 매일 수집해 풀에 쌓고,
금요일에 이 모듈로 7일치를 합치면 누락이 사라진다.
(DART·나라장터·식약처는 날짜범위 API라 롤오프가 없어 금요일 일괄 수집으로 충분.)

멱등성: 같은 입력 → 같은 출력. dedup 키는 id → url → 본문 해시 순 fallback.
"""
from __future__ import annotations

import glob as _glob
import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _as_date(d):
    """str('YYYY-MM-DD') 또는 date → date."""
    if isinstance(d, str):
        return datetime.strptime(d[:10], "%Y-%m-%d").date()
    return d


def _key(item: dict) -> str:
    """중복제거 키. id → url → 정렬 JSON 해시 순."""
    k = item.get("id") or item.get("url")
    if k:
        return str(k)
    raw = json.dumps(item, sort_keys=True, ensure_ascii=False)
    return "h:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def dedup_by_id(items):
    """첫 출현 우선 dedup (입력 순서 보존)."""
    seen: set[str] = set()
    out: list[dict] = []
    for it in items:
        k = _key(it)
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def window_dates(today, days: int):
    """[today-(days-1) .. today] date 리스트 (오름차순). days>=1."""
    today = _as_date(today)
    days = max(1, days)
    return [today - timedelta(days=n) for n in range(days - 1, -1, -1)]


def _read_jsonl(path: Path):
    items: list[dict] = []
    if not path.exists():
        return items
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return items


def write_jsonl(items, out_path) -> int:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    return len(items)


def pool_path(pool_pattern: str, day) -> str:
    """'data/pool/rss_{date}.jsonl' + 날짜 → 실제 경로."""
    return pool_pattern.replace("{date}", _as_date(day).strftime("%Y-%m-%d"))


def add_to_pool(src_path, pool_pattern: str, today) -> tuple[int, str]:
    """오늘 필터 결과(src_path)를 풀(pool_pattern@today)에 적재(정규화 복사). (건수, 경로)."""
    items = _read_jsonl(Path(src_path))
    dst = pool_path(pool_pattern, today)
    write_jsonl(items, dst)
    return len(items), dst


def merge_window(pattern: str, today, days: int):
    """pattern 내 '{date}' 를 윈도우 각 날짜로 치환해 읽어 병합+dedup.

    Returns: (merged_items, stats)
      stats = {files_found, files_missing, raw_count, merged_count,
               dup_removed, per_day, window}
    """
    dates = window_dates(today, days)
    raw: list[dict] = []
    found = missing = 0
    per_day: dict[str, int] = {}
    for d in dates:
        p = Path(pattern.replace("{date}", d.strftime("%Y-%m-%d")))
        if p.exists():
            items = _read_jsonl(p)
            found += 1
            per_day[d.strftime("%Y-%m-%d")] = len(items)
            raw.extend(items)
        else:
            missing += 1
    merged = dedup_by_id(raw)
    stats = {
        "files_found": found,
        "files_missing": missing,
        "raw_count": len(raw),
        "merged_count": len(merged),
        "dup_removed": len(raw) - len(merged),
        "per_day": per_day,
        "window": [d.strftime("%Y-%m-%d") for d in dates],
    }
    return merged, stats


def prune_old(pattern: str, today, keep_days: int) -> list[str]:
    """윈도우(keep_days) 밖의 날짜 파일 삭제. 삭제 경로 리스트 반환.

    pattern 의 '{date}' 자리를 glob 로 훑어 파일명 날짜를 파싱, cutoff 미만이면 삭제.
    """
    today = _as_date(today)
    cutoff = today - timedelta(days=max(1, keep_days) - 1)
    removed: list[str] = []
    for fp in _glob.glob(pattern.replace("{date}", "*")):
        m = _DATE_RE.search(Path(fp).name)
        if not m:
            continue
        try:
            fd = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if fd < cutoff:
            try:
                Path(fp).unlink()
                removed.append(fp)
            except OSError:
                pass
    return removed
