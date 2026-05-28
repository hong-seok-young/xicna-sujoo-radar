"""Stage 0 (보조): 식약처 의약품 GMP 적합판정서 발급현황 수집기.

영업 활용 (시그널 단계):
    GMP 적합판정 = 공장 완공 후 인증 단계 → 시공 발주 시그널로는 늦음.
    하지만 다음 두 가지로 활용 가능:

    1) **회사 트래킹**: 신규 GMP 발급 = "이 회사 향후 12~24개월 내 증설 가능성".
       영업팀 contact 리스트 + DART/RSS 매칭 시 시그널 강화.
    2) **산업동 화이트리스트 보강**: 활성 GMP 공장 소재지 → 제약/바이오 카테고리
       동 시그널 추가. EAIS 인허가 검색 시 단가 산정 정확도 ↑.

데이터 소스:
    공공데이터포털 — 식품의약품안전처 의약품GMP적합판정서발급현황
    Endpoint: https://apis.data.go.kr/1471000/DrugGmpStbltJgmtIssuStusService/getDrugGmpStbltJgmtIssuStusInq
    필수: serviceKey (공공데이터포털 발급, EAIS 와 동일 키 재사용)
    응답 필드: BSSH_NM(업체명), FCTR_ADDR(공장소재지), KGMP_BGMP_NAME(완제/원료),
              GMP_INGR_MM_GROUP_NAME(제형군), VLD_PRD_YMD(유효기간)
    트래픽: 10,000/일 (넉넉)

주의 — 응답에 '발급일자' 필드가 없음 (공공데이터포털 문서 기준):
    매주 풀 스냅샷 → 이전 스냅샷과 diff 로 '신규 발급' 식별. 유효기간 5년이면
    GMP_발급_추정일 = VLD_PRD_YMD - 5년 으로 역산도 가능 (응답 확인 후 결정).

사용법:
    python -m src.stage0_collect.mfds_gmp           # 풀 스냅샷 + diff
    python -m src.stage0_collect.mfds_gmp --insecure
    python -m src.stage0_collect.mfds_gmp --full    # diff 생략, 전체 dump
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import time
import urllib3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

# MFDS GMP 항목은 Article 스키마에 안 맞는 필드 多 (BIZRNO, addr, vld 등) — dict 그대로 jsonl
# write_jsonl 은 Article 전용이라 우회

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))

BASE_URL = "https://apis.data.go.kr/1471000/DrugGmpStbltJgmtIssuStusService"
LIST_ENDPOINT = f"{BASE_URL}/getDrugGmpStbltJgmtIssuStusInq"

CACHE_DIR = Path("data/cache/mfds_gmp")
CACHE_SNAPSHOT = CACHE_DIR / "snapshot_latest.json"
CACHE_PREV = CACHE_DIR / "snapshot_prev.json"

REQUEST_TIMEOUT = 20
USER_AGENT = "sujoo-radar/0.1 (xicna mfds-gmp client)"
NUM_ROWS_PER_PAGE = 100   # API 기본 3 → 100 으로


# ─────────────────────────────────────────────────────────
# 1. API 호출 (페이지)
# ─────────────────────────────────────────────────────────


def fetch_page(api_key: str, page_no: int, verify_ssl: bool,
               bssh_nm: str = "") -> tuple[list[dict], int, str]:
    """1 페이지 조회. (items, total, error_msg). error_msg='' 면 정상."""
    params = {
        "serviceKey": api_key,
        "pageNo": str(page_no),
        "numOfRows": str(NUM_ROWS_PER_PAGE),
        "type": "json",
    }
    if bssh_nm:
        params["BSSH_NM"] = bssh_nm
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        r = requests.get(LIST_ENDPOINT, params=params, timeout=REQUEST_TIMEOUT,
                         verify=verify_ssl, headers=headers)
        r.raise_for_status()
        d = r.json()
    except requests.RequestException as e:
        # 사내망 SSL Inspection(self-signed cert in chain) 대응 — RSS 수집기와 동일 패턴.
        # verify=True 1차 실패 시 verify=False 로 재시도해야 회사망에서 수집된다.
        if verify_ssl:
            try:
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                r = requests.get(LIST_ENDPOINT, params=params, timeout=REQUEST_TIMEOUT,
                                 verify=False, headers=headers)
                r.raise_for_status()
                d = r.json()
            except requests.RequestException as e2:
                return [], 0, f"http_err:{e2}"
            except ValueError:
                return [], 0, "json_err"
        else:
            return [], 0, f"http_err:{e}"
    except ValueError:
        return [], 0, "json_err"

    # 공공데이터포털 응답 구조는 데이터셋마다 다름 — body.items 또는 response.body.items
    body = None
    # 일반적 패턴: { "response": { "header": {...}, "body": { "items": [...], "totalCount": N } } }
    if isinstance(d, dict) and "response" in d:
        try:
            header = d["response"]["header"]
            code = header.get("resultCode") or header.get("RESULT_CODE")
            if code not in ("00", "0", None):
                return [], 0, f"api_err:{code}:{header.get('resultMsg') or header.get('RESULT_MSG','')}"
            body = d["response"]["body"]
        except (KeyError, TypeError):
            return [], 0, "schema_err:response.header/body"
    # 변종 패턴: { "header": {...}, "body": {...} } 또는 { "items": [...] }
    elif isinstance(d, dict) and "body" in d:
        body = d["body"]
    elif isinstance(d, dict) and ("items" in d or "Items" in d):
        body = d
    else:
        return [], 0, f"schema_err:unknown_top_keys={list(d.keys()) if isinstance(d, dict) else type(d).__name__}"

    if body is None:
        return [], 0, "schema_err:body_none"

    total = int(body.get("totalCount") or body.get("TOTAL_COUNT") or 0)
    raw_items = body.get("items") or body.get("Items") or []
    # items 는 list 일 때도 있고 {"item": [...]} 일 때도 있음
    if isinstance(raw_items, dict):
        raw_items = raw_items.get("item") or raw_items.get("Item") or []
    if not isinstance(raw_items, list):
        # 단일 dict 응답
        raw_items = [raw_items] if isinstance(raw_items, dict) else []
    return raw_items, total, ""


def fetch_all(api_key: str, verify_ssl: bool,
              max_pages: int = 200, pause_sec: float = 0.2) -> list[dict]:
    """모든 페이지 fetch — 전체 GMP 명단 풀 스냅샷."""
    all_items: list[dict] = []
    page = 1
    while page <= max_pages:
        items, total, err = fetch_page(api_key, page, verify_ssl)
        if err:
            logger.error("page=%d 실패: %s", page, err)
            break
        if not items:
            break
        all_items.extend(items)
        logger.info("page=%d  fetched=%d  누계=%d  total=%d", page, len(items), len(all_items), total)
        if total and len(all_items) >= total:
            break
        page += 1
        time.sleep(pause_sec)
    return all_items


# ─────────────────────────────────────────────────────────
# 2. 스냅샷 캐시 + diff
# ─────────────────────────────────────────────────────────


def _item_key(it: dict) -> str:
    """동일 GMP 식별 키 — 업체명 + 공장소재지 + 완제/원료 + 제형군."""
    parts = [
        (it.get("BSSH_NM") or "").strip(),
        (it.get("FCTR_ADDR") or "").strip(),
        (it.get("KGMP_BGMP_NAME") or "").strip(),
        (it.get("GMP_INGR_MM_GROUP_NAME") or "").strip(),
    ]
    return hashlib.md5("||".join(parts).encode("utf-8")).hexdigest()[:16]


def load_snapshot(p: Path) -> dict[str, dict]:
    """스냅샷 → {key: item} 매핑."""
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return {_item_key(it): it for it in d.get("items", [])}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("스냅샷 로드 실패 %s: %s", p, e)
        return {}


def save_snapshot(items: list[dict], p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "snapshot_at": datetime.now(KST).isoformat(),
        "count": len(items),
        "items": items,
    }, ensure_ascii=False), encoding="utf-8")


def diff_snapshots(prev: dict[str, dict],
                   curr: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """신규 추가 / 만료(사라짐) 항목 반환."""
    added = [it for k, it in curr.items() if k not in prev]
    removed = [it for k, it in prev.items() if k not in curr]
    return added, removed


# ─────────────────────────────────────────────────────────
# 3. Article 변환 (daily_report 입력 형식)
# ─────────────────────────────────────────────────────────


def _make_id(item: dict) -> str:
    return f"mfds:{_item_key(item)}"


def to_article(item: dict, now_iso: str, *, is_new: bool = True) -> dict:
    """GMP 항목 → daily_report 형식 dict.

    URL 정책:
      MFDS API 응답에는 항목별 상세 페이지 URL 이 없음. 영업팀이 원문 확인하려면:
        1) 공공데이터포털 데이터셋 페이지 (출처 데이터셋 자체)
        2) 의약품안전나라 통합검색 (회사명 query)
        3) 네이버 회사명 검색 (영업 컨택용 가장 빠른 방법)
      카드의 메인 url 은 (3) 네이버 회사 검색 — 영업 활용도 최고.
      "데이터 출처" 는 카드 메타에 별도 표시 (data_source_url).
    """
    from urllib.parse import quote
    bssh = (item.get("BSSH_NM") or "업체미상").strip()
    addr = (item.get("FCTR_ADDR") or "").strip()
    kind = (item.get("KGMP_BGMP_NAME") or "").strip()        # 완제/원료
    form = (item.get("GMP_INGR_MM_GROUP_NAME") or "").strip()  # 제형군/제조방법
    vld = (item.get("VLD_PRD_YMD") or "").strip()             # 유효기간
    bizrno = (item.get("BIZRNO") or "").strip()              # 사업자번호

    # 유효기간 - GMP 3년 기준 발급 추정 (실제 GMP 적합판정 유효기간은 3년)
    issued_est = ""
    if vld and len(vld) >= 4:
        try:
            y = int(vld[:4])
            tail = vld[4:] if len(vld) > 4 else ""
            issued_est = f"{y-3}{tail}"
        except ValueError:
            pass

    flag = "🆕 신규 발급" if is_new else "기존"
    title = f"[{flag}] {bssh} — {kind} {form} (공장: {addr[:30]})"
    content = " | ".join(filter(None, [
        f"업체: {bssh}",
        f"사업자번호: {bizrno}" if bizrno else "",
        f"공장소재지: {addr}",
        f"구분: {kind}",
        f"제형/방법: {form}",
        f"유효기간: {vld}",
        f"발급추정: {issued_est}" if issued_est else "",
    ]))

    # 의약품안전나라는 SPA 라 URL 키워드 검색 자동적용 X (215건 전체가 뜸).
    # 영업적으로 더 의미있는 두 가지 링크를 카드 렌더에서 직접 생성 (daily_report_html.py).
    # 여기는 source URL 만 유지 (참고용).
    naver_gmp = f"https://search.naver.com/search.naver?query={quote(bssh + ' GMP')}"

    return {
        "id": _make_id(item),
        "source": "mfds.go.kr",
        "url": naver_gmp,                # 참고 URL — 실제 카드 링크는 daily_report 에서 재구성
        "title": title,
        "content": content,
        "published_at": now_iso,         # 발급일 미상 — 수집일로 대체
        "collected_at": now_iso,
        "category": "제약/바이오",
        "categories": ["제약/바이오"],
        "bssh": bssh,
        "bizrno": bizrno,
        "addr": addr,
        "kind": kind,
        "form": form,
        "vld": vld,
        "issued_est": issued_est,
        "is_new": is_new,                # 직전 스냅샷 대비 신규 항목 여부 (daily_report 필터용)
    }


# ─────────────────────────────────────────────────────────
# 4. CLI
# ─────────────────────────────────────────────────────────


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--insecure", action="store_true", help="SSL 검증 disable")
    ap.add_argument("--full", action="store_true",
                    help="(default) 전체 풀 dump. 호환성 위해 남김.")
    ap.add_argument("--diff-only", action="store_true",
                    help="이전 스냅샷 대비 신규 발급만 dump. 매주 신규만 알고 싶을 때.")
    ap.add_argument("--out-dir", default="data/raw",
                    help="JSONL 출력 디렉토리")
    args = ap.parse_args()

    if args.insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    load_dotenv()
    api_key = os.environ.get("EAIS_API_KEY", "").strip()
    if not api_key:
        logger.error("EAIS_API_KEY 가 .env 에 설정되지 않았습니다 — 공공데이터포털 키 재사용")
        return

    today = datetime.now(KST).strftime("%Y-%m-%d")
    now_iso = datetime.now(KST).isoformat()
    out_path = Path(args.out_dir) / f"mfds_gmp_{today}.jsonl"

    logger.info("=== MFDS GMP 풀 스냅샷 시작 ===")
    items = fetch_all(api_key, verify_ssl=not args.insecure)
    logger.info("전체 fetched: %d 건", len(items))

    if not items:
        logger.error("0건 — 인증키 활용신청 안 됐을 가능성. data.go.kr/data/15097207 활용신청 확인")
        return

    # 직전 스냅샷 → prev 로 백업, 현재 → latest
    if CACHE_SNAPSHOT.exists():
        CACHE_SNAPSHOT.replace(CACHE_PREV)
    save_snapshot(items, CACHE_SNAPSHOT)

    # 풀 dump 가 기본 — 영업팀이 매주 풀 명단 보면서 신규(is_new) 만 강조 보려는 케이스.
    # --diff-only 명시 시에만 신규만 dump (이메일 알림 등 용도).
    prev = load_snapshot(CACHE_PREV)
    curr = {_item_key(it): it for it in items}
    added, removed = diff_snapshots(prev, curr)
    new_keys = {_item_key(it) for it in added}
    logger.info("diff: 신규=%d 만료=%d (prev=%d → curr=%d)",
                len(added), len(removed), len(prev), len(curr))

    if args.diff_only:
        articles = [to_article(it, now_iso, is_new=True) for it in added]
        logger.info("--diff-only 모드: 신규 %d 건 dump", len(articles))
    else:
        # 기본: 전체 풀 dump, 신규 추가건은 is_new=True 플래그
        articles = [
            to_article(it, now_iso, is_new=(_item_key(it) in new_keys))
            for it in items
        ]
        logger.info("풀 dump: 전체 %d 건 (신규 %d 건 포함)", len(articles), len(added))

    # dict 그대로 jsonl 저장
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for art in articles:
            f.write(json.dumps(art, ensure_ascii=False) + "\n")
    logger.info("저장: %s  (%d 건)", out_path, len(articles))


if __name__ == "__main__":
    main()
