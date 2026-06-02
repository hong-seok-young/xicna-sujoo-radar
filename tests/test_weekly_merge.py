"""주간 취합(weekly_merge) 테스트 — 윈도우 병합·중복제거·풀 적재·정리."""
from __future__ import annotations

import json
from datetime import date

from src.common.weekly_merge import (
    add_to_pool,
    dedup_by_id,
    merge_window,
    prune_old,
    window_dates,
    write_jsonl,
)


def _w(path, items):
    write_jsonl(items, path)


def test_dedup_keeps_first_and_order():
    items = [
        {"id": "a", "title": "1"},
        {"id": "b", "title": "2"},
        {"id": "a", "title": "1-dup"},  # 중복 → 제거
        {"id": "c", "title": "3"},
    ]
    out = dedup_by_id(items)
    assert [x["id"] for x in out] == ["a", "b", "c"]
    assert out[0]["title"] == "1"  # 첫 출현 보존


def test_dedup_url_fallback_then_hash():
    # id 없으면 url, 둘 다 없으면 본문 해시
    items = [
        {"url": "http://x/1"},
        {"url": "http://x/1"},          # url 중복 제거
        {"title": "no-id-no-url"},
        {"title": "no-id-no-url"},      # 동일 본문 해시 → 제거
        {"title": "different"},
    ]
    out = dedup_by_id(items)
    assert len(out) == 3


def test_window_dates():
    ds = window_dates("2026-06-02", 7)
    assert len(ds) == 7
    assert ds[0] == date(2026, 5, 27)
    assert ds[-1] == date(2026, 6, 2)
    assert ds == sorted(ds)  # 오름차순


def test_merge_window_dedups_across_days(tmp_path):
    pat = str(tmp_path / "rss_{date}.jsonl")
    _w(tmp_path / "rss_2026-06-01.jsonl", [{"id": "a"}, {"id": "b"}])
    _w(tmp_path / "rss_2026-06-02.jsonl", [{"id": "b"}, {"id": "c"}])  # b 중복
    merged, stats = merge_window(pat, "2026-06-02", 2)
    ids = sorted(x["id"] for x in merged)
    assert ids == ["a", "b", "c"]
    assert stats["raw_count"] == 4
    assert stats["merged_count"] == 3
    assert stats["dup_removed"] == 1
    assert stats["files_found"] == 2 and stats["files_missing"] == 0


def test_merge_window_skips_missing_days(tmp_path):
    pat = str(tmp_path / "rss_{date}.jsonl")
    # 7일 윈도우 중 하루만 존재 (나머지 6일 결번)
    _w(tmp_path / "rss_2026-06-02.jsonl", [{"id": "a"}, {"id": "b"}])
    merged, stats = merge_window(pat, "2026-06-02", 7)
    assert stats["files_found"] == 1
    assert stats["files_missing"] == 6
    assert stats["merged_count"] == 2  # 결번은 그냥 건너뜀(에러 없음)


def test_prune_old_removes_outside_window(tmp_path):
    pat = str(tmp_path / "rss_{date}.jsonl")
    for d in ["2026-05-20", "2026-05-28", "2026-06-01", "2026-06-02"]:
        _w(tmp_path / f"rss_{d}.jsonl", [{"id": d}])
    # keep_days=7 (윈도우 05-27 ~ 06-02) → 05-20 만 삭제 대상
    removed = prune_old(pat, "2026-06-02", 7)
    assert len(removed) == 1 and "2026-05-20" in removed[0]
    assert not (tmp_path / "rss_2026-05-20.jsonl").exists()
    assert (tmp_path / "rss_2026-05-28.jsonl").exists()
    assert (tmp_path / "rss_2026-06-02.jsonl").exists()


def test_add_to_pool_copies_and_counts(tmp_path):
    src = tmp_path / "filtered.jsonl"
    _w(src, [{"id": "a"}, {"id": "b"}, {"id": "c"}])
    pat = str(tmp_path / "pool" / "rss_{date}.jsonl")
    n, dst = add_to_pool(src, pat, "2026-06-02")
    assert n == 3
    assert "rss_2026-06-02.jsonl" in dst
    # 적재된 풀 파일을 다시 읽어 동일 건수 확인
    lines = [json.loads(l) for l in open(dst, encoding="utf-8").read().splitlines() if l.strip()]
    assert len(lines) == 3
