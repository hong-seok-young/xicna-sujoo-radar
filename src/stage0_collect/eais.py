"""Stage 0: 건축HUB 건축인허가 수집기 — 전국 + 450억 임계값 필터.

자이씨앤에이 시공 영업의 ★★ 신호 — 건축인허가는 착공 1~6개월 전 단계.
이 시점에 시공사가 결정되는 경우가 많아 가장 빠른 영업 골든타임.

데이터 소스:
    공공데이터포털 — 국토교통부 건축HUB 건축인허가정보 서비스
    Endpoint: https://apis.data.go.kr/1613000/ArchPmsHubService/getApBasisOulnInfo
    필수: sigunguCd(시군구 5자리) + bjdongCd(법정동 5자리)

전국 운영 전략 (1,000회/일 API 한도 대응):
    - config/legal_dong.csv 에 전국 동/읍/면 ~5,961개 미리 임베드
    - 데이터/cache/eais/{sigungu}_{bjdong}.json 영구 캐시 (인허가는 한번 발생하면 안 사라짐)
    - data/cache/eais/_index.json 인덱스로 "마지막 조회 시각" 추적
    - 매 run 마다 우선순위로 ~900개 쿼리 후 quota 소진:
        1순위) 한번도 조회 안 된 동/읍/면 (untouched)
        2순위) "활성" 동 — 직전 조회에서 산업시설 인허가 1건 이상 (refresh_active_days 경과)
        3순위) "빈" 동 — 산업시설 인허가 0건 (refresh_empty_days 경과, 더 길게)
    - 6일 전후로 전국 1차 스캔 완료 → 이후 활성 동 위주 매일 갱신

영업 가치 필터:
    1) 용도 필터: 공장/창고/연구소/근생 등 (TARGET_PURPOSE_KEYWORDS)
    2) 추정공사비 ≥ 450억 (mainPurpsCdNm × totArea × 카테고리별 단가, _eais_cost.py 참고)
    3) 인허가일 (archPmsDay) 최근 N일 이내 (기본 30일)

사용법:
    python -m src.stage0_collect.eais                          # 일일 운영 (quota 900)
    python -m src.stage0_collect.eais --full-sweep             # 미조회 동 전부 (quota 무시)
    python -m src.stage0_collect.eais --days 60                # 최근 60일
    python -m src.stage0_collect.eais --threshold-eok 300      # 임계값 300억으로 낮춤
    python -m src.stage0_collect.eais --quota 500              # quota 500 으로 제한
    python -m src.stage0_collect.eais --sigungu 41370 --bjdong 10300  # 특정 동 디버그
    python -m src.stage0_collect.eais --no-cost-filter         # 추정공사비 필터 끔 (대량 결과)
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests
import urllib3
from dotenv import load_dotenv

from ..common.io import write_jsonl
from ..common.schema import Article
from ._eais_cost import (
    DEFAULT_THRESHOLD_MAN,
    MAX_COST_MAN,
    estimate_cost_man,
    format_cost,
    passes_threshold,
)

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))

BASE_URL = "https://apis.data.go.kr/1613000/ArchPmsHubService"
LIST_ENDPOINT = f"{BASE_URL}/getApBasisOulnInfo"

REQUEST_TIMEOUT = 25
USER_AGENT = "sujoo-radar/0.1 (xicna eais client)"
NUM_ROWS_PER_PAGE = 1000   # 공공API 일반 상한
MAX_PAGES_PER_LOCATION = 10  # 안전망 — 한 동 최대 10,000건 (실제로는 1페이지면 끝나는 곳 多)

LEGAL_DONG_CSV = Path("config/legal_dong.csv")
INDUSTRIAL_DONG_CSV = Path("config/industrial_dongs.csv")   # 산업 후보 ~1,200 동
CACHE_DIR = Path("data/cache/eais")
INDEX_PATH = CACHE_DIR / "_index.json"

# 자이씨앤에이 시공 영업 관심 용도 (mainPurpsCdNm 매칭)
TARGET_PURPOSE_KEYWORDS = [
    "공장", "창고", "연구소", "연구시설", "교육연구",
    "제1종근린생활시설", "제2종근린생활시설",  # 소규모 공장이 종종 여기로 분류
    "발전시설", "위험물", "자원순환",
    "병원", "의료시설",  # 제약/바이오 분류 보조
]

DEFAULT_QUOTA = 900            # 일일 1,000 API 한도 - 100 안전 마진
# 활성 동 재조회 주기 — 매일 발사 전제. lookback window(7일) 보다 작아야 인허가 누락 없음.
# 활성 동 ~1,500 / 3일 = ~500 API/일. quota 900 의 57% (안전 마진 큼).
DEFAULT_REFRESH_ACTIVE_DAYS = 3
# 빈 동 재조회 주기 — 새 산업지 진입 latency. 빈 동 ~280 / 14일 = ~20 API/일.
# (이전 30일이었으나 매일 발사로 전환하며 단축)
DEFAULT_REFRESH_EMPTY_DAYS = 14

# ── 데이터 정합성 가드 ──
# 단일 인허가 연면적 100만㎡ 초과는 단위 오류·산단 합산 오류로 추정 → 컷
MAX_AREA_M2 = 1_000_000

# 영업 가치 있는 건축구분만 통과 — 대수선/용도변경/이전 등은 새 시공 발주 아님
# (archGbCdNm 예: "신축", "증축", "대수선", "용도변경", "이전", "개축", "재축")
VALID_ARCH_GB_KEYWORDS: tuple[str, ...] = ("신축", "증축")


# ─────────────────────────────────────────────────────────
# 1. 법정동 로드 + 캐시 인덱스
# ─────────────────────────────────────────────────────────


def load_legal_dongs(csv_path: Path | None = None, *, use_industrial: bool = True) -> list[dict]:
    """동 리스트 로드.

    use_industrial=True (기본): config/industrial_dongs.csv (산업 후보 ~1,200동) 우선 사용.
                                없으면 legal_dong.csv 전체로 fallback.
    use_industrial=False: 전국 5,961개 동 모두 (--all-dongs CLI 옵션).
    """
    if csv_path is None:
        if use_industrial and INDUSTRIAL_DONG_CSV.exists():
            csv_path = INDUSTRIAL_DONG_CSV
            logger.info("산업 후보 동 사용: %s", csv_path)
        else:
            csv_path = LEGAL_DONG_CSV
            logger.info("전국 동 사용: %s", csv_path)
    if not csv_path.exists():
        logger.error("법정동 CSV 없음: %s", csv_path)
        logger.error("scripts/build_legal_dong.py / build_industrial_dongs.py 로 생성하세요.")
        return []
    rows: list[dict] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            rows.append(row)
    return rows


def load_index() -> dict:
    """cache index 로드. 형식:
        { "41370-10300": {"fetched_at": "...", "total_count": 123, "has_industrial": true} }
    """
    if not INDEX_PATH.exists():
        return {}
    try:
        return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("index 로드 실패 %s: %s — 빈 인덱스로 시작", INDEX_PATH, e)
        return {}


def save_index(index: dict) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def cache_path_for(sigungu_cd: str, bjdong_cd: str) -> Path:
    return CACHE_DIR / f"{sigungu_cd}_{bjdong_cd}.json"


def load_cache(sigungu_cd: str, bjdong_cd: str) -> Optional[dict]:
    p = cache_path_for(sigungu_cd, bjdong_cd)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("캐시 로드 실패 %s: %s", p, e)
        return None


def save_cache(sigungu_cd: str, bjdong_cd: str, payload: dict) -> None:
    p = cache_path_for(sigungu_cd, bjdong_cd)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# ─────────────────────────────────────────────────────────
# 2. API 호출 (페이지 한 번)
# ─────────────────────────────────────────────────────────


def fetch_page(api_key: str, sigungu_cd: str, bjdong_cd: str,
               page_no: int, verify_ssl: bool) -> tuple[list[dict], int, str]:
    """1 페이지 조회. (items, total, error_msg) 반환. error_msg='' 면 정상."""
    params = {
        "serviceKey": api_key,
        "sigunguCd": sigungu_cd,
        "bjdongCd": bjdong_cd,
        "pageNo": str(page_no),
        "numOfRows": str(NUM_ROWS_PER_PAGE),
        "_type": "json",
    }
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        r = requests.get(LIST_ENDPOINT, params=params, timeout=REQUEST_TIMEOUT,
                         verify=verify_ssl, headers=headers)
        r.raise_for_status()
        d = r.json()
    except requests.RequestException as e:
        # 사내망 SSL Inspection(self-signed cert) 대응 — apis.data.go.kr 는 MFDS 와
        # 같은 호스트라 verify=True 가 깨질 수 있음. 1차 실패 시 verify=False 재시도.
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

    try:
        header = d["response"]["header"]
        result_code = header.get("resultCode")
        if result_code != "00":
            return [], 0, f"api_err:{result_code}:{header.get('resultMsg','')}"
        body = d["response"]["body"]
        items = body.get("items", {}).get("item", []) or []
        if isinstance(items, dict):
            items = [items]
        total = int(body.get("totalCount") or 0)
        return items, total, ""
    except (KeyError, TypeError) as e:
        return [], 0, f"shape_err:{e}"


def fetch_dong_full(api_key: str, sigungu_cd: str, bjdong_cd: str,
                     verify_ssl: bool) -> tuple[list[dict], int, int, str]:
    """한 (sigungu, bjdong) 의 모든 페이지 fetch. (items, total, calls_used, err) 반환."""
    all_items: list[dict] = []
    calls = 0
    page = 1
    total = 0
    err = ""
    while page <= MAX_PAGES_PER_LOCATION:
        items, total, err = fetch_page(api_key, sigungu_cd, bjdong_cd, page, verify_ssl)
        calls += 1
        if err:
            return all_items, total, calls, err
        all_items.extend(items)
        if len(items) < NUM_ROWS_PER_PAGE:
            break
        page += 1
        time.sleep(0.15)
    return all_items, total, calls, err


# ─────────────────────────────────────────────────────────
# 3. 동 선택 우선순위 — 일일 quota 안에서 무엇을 조회할지
# ─────────────────────────────────────────────────────────


def pick_dongs_to_query(
    legal_dongs: list[dict],
    index: dict,
    quota: int,
    now: datetime,
    refresh_active_days: int = DEFAULT_REFRESH_ACTIVE_DAYS,
    refresh_empty_days: int = DEFAULT_REFRESH_EMPTY_DAYS,
) -> list[dict]:
    """quota 만큼 동 선택 — untouched → stale active → stale empty 우선순위.

    note: 한 동이 multi-page (인허가 多) 일 경우 실제 API 호출 더 들 수 있음.
          반환 list 는 "조회할 동" 후보일 뿐 실제 quota 는 호출하면서 차감.
    """
    untouched: list[dict] = []
    stale_active: list[tuple[float, dict]] = []  # (fetched_age, dong)
    stale_empty: list[tuple[float, dict]] = []
    fresh = 0

    for d in legal_dongs:
        key = f"{d['sigungu_cd']}-{d['bjdong_cd']}"
        idx = index.get(key)
        if idx is None:
            untouched.append(d)
            continue
        fetched_at_s = idx.get("fetched_at")
        if not fetched_at_s:
            untouched.append(d)
            continue
        try:
            fetched_at = datetime.fromisoformat(fetched_at_s)
        except ValueError:
            untouched.append(d)
            continue
        age_days = (now - fetched_at).total_seconds() / 86400
        has_industrial = bool(idx.get("has_industrial"))
        if has_industrial:
            if age_days >= refresh_active_days:
                stale_active.append((age_days, d))
            else:
                fresh += 1
        else:
            if age_days >= refresh_empty_days:
                stale_empty.append((age_days, d))
            else:
                fresh += 1

    # untouched 안에서도 산업 가치 정렬: 반도체_디스플레이/이차전지/제약_바이오 우선
    # → quota 작아도 핵심 산업 클러스터부터 닿음.
    _CAT_RANK = {
        "반도체_디스플레이": 0, "이차전지": 0,           # 영업 핵심
        "제약_바이오": 1,
        "연구개발": 2, "일반제조": 2,
        "식품": 3,
    }
    def _untouched_rank(d: dict) -> int:
        cats = (d.get("categories", "") or "").split("|")
        if not cats or cats == [""]:
            return 99
        return min((_CAT_RANK.get(c, 99) for c in cats), default=99)

    untouched.sort(key=_untouched_rank)
    stale_active.sort(key=lambda x: -x[0])  # 오래된 것 우선
    stale_empty.sort(key=lambda x: -x[0])

    picked: list[dict] = []
    picked.extend(untouched)                          # untouched 전체 (quota 까지)
    picked.extend(d for _, d in stale_active)
    picked.extend(d for _, d in stale_empty)

    logger.info("동 선택: untouched=%d, stale_active=%d, stale_empty=%d, fresh=%d, quota=%d",
                len(untouched), len(stale_active), len(stale_empty), fresh, quota)

    return picked[:quota]


# ─────────────────────────────────────────────────────────
# 4. 인허가 → Article 변환 + 필터 체인
# ─────────────────────────────────────────────────────────


def _make_id(mgm_pk) -> str:
    return hashlib.md5(f"eais:{mgm_pk}".encode("utf-8")).hexdigest()[:16]


# archPmsDay 손상 데이터 가드 — 연도 범위 밖(예: '50140514') 은 컷.
# EAIS 응답에 연도 5014 같은 깨진 날짜가 섞여 있는데, 문자열 비교라 윈도우 컷
# (d < threshold_str) 을 통과해버려 노이즈로 잡힌다. 정상 연도만 통과시킨다.
MIN_VALID_YEAR = 2000


def _sane_archpms_day(s: str) -> str | None:
    """archPmsDay 8자리 + 연도 [2000, 올해+1] 범위면 정규화된 8자리 반환, 아니면 None."""
    s = (s or "").strip()[:8]
    if len(s) < 8 or not s.isdigit():
        return None
    y = int(s[:4])
    if y < MIN_VALID_YEAR or y > datetime.now(KST).year + 1:
        return None
    return s


def _parse_date_yyyymmdd(s: str) -> datetime | None:
    s = _sane_archpms_day(s)
    if s is None:
        return None
    try:
        return datetime.strptime(s, "%Y%m%d").replace(tzinfo=KST)
    except ValueError:
        return None


def _is_target_purpose(purpose_name: str) -> bool:
    if not purpose_name:
        return False
    return any(k in purpose_name for k in TARGET_PURPOSE_KEYWORDS)


def _to_article(item: dict, location_label: str, est_cost_man: int,
                category: str) -> Article | None:
    """EAIS 응답 1건 → Article (추정공사비, 카테고리 포함)."""
    mgm_pk = item.get("mgmPmsrgstPk")
    if not mgm_pk:
        return None

    plat_plc = (item.get("platPlc") or "").strip()
    bld_nm = (item.get("bldNm") or "").strip()
    purpose = (item.get("mainPurpsCdNm") or "").strip()
    arch_gb = (item.get("archGbCdNm") or "").strip()
    arch_day = item.get("archPmsDay") or ""

    title_purp = purpose or "용도미상"
    title_arch = arch_gb or "건축"
    title_bld = f" «{bld_nm}»" if bld_nm else ""
    title_loc = plat_plc or location_label
    cost_tag = format_cost(est_cost_man)
    title = f"[{category} 추정{cost_tag}] {title_arch}-{title_purp}{title_bld} — {title_loc}"

    parts = [
        f"주소: {plat_plc}",
        f"주용도: {purpose}",
        f"건축구분: {arch_gb}",
        f"인허가일: {arch_day}",
        f"추정공사비: {cost_tag} (카테고리: {category})",
    ]
    if item.get("totArea") and str(item.get("totArea")) not in ("0", "0.0"):
        parts.append(f"연면적: {item['totArea']}㎡")
    if item.get("archArea") and str(item.get("archArea")) not in ("0", "0.0"):
        parts.append(f"건축면적: {item['archArea']}㎡")
    if item.get("platArea") and str(item.get("platArea")) not in ("0", "0.0"):
        parts.append(f"대지면적: {item['platArea']}㎡")
    if item.get("mainBldCnt") and str(item.get("mainBldCnt")) not in ("0",):
        parts.append(f"주건축물수: {item['mainBldCnt']}동")
    # 추가 필드 — 영업/시공 가치 (EAIS API 응답에 들어있는 미수집 필드)
    if item.get("atchBldDongCnt") and str(item.get("atchBldDongCnt")) not in ("0",):
        parts.append(f"부속건축물수: {item['atchBldDongCnt']}동")
    jiyuk = (item.get("jiyukCdNm") or "").strip()
    if jiyuk:
        parts.append(f"지역지구: {jiyuk}")
    jimok = (item.get("jimokCdNm") or "").strip()
    if jimok:
        parts.append(f"지목: {jimok}")
    guyuk = (item.get("guyukCdNm") or "").strip()
    if guyuk:
        parts.append(f"구역: {guyuk}")
    bcrat = item.get("bcRat")
    if bcrat and str(bcrat) not in ("0", "0.0"):
        parts.append(f"건폐율: {bcrat}%")
    vlrat = item.get("vlRat")
    if vlrat and str(vlrat) not in ("0", "0.0"):
        parts.append(f"용적률: {vlrat}%")
    pkng = item.get("totPkngCnt")
    if pkng and str(pkng) not in ("0",):
        parts.append(f"주차장: {pkng}대")
    hhld = item.get("hhldCnt")
    if hhld and str(hhld) not in ("0",):
        parts.append(f"세대수: {hhld}세대")
    if item.get("stcnsSchedDay"):
        parts.append(f"착공예정: {item['stcnsSchedDay']}")
    if item.get("realStcnsDay"):
        parts.append(f"실제착공: {item['realStcnsDay']}")
    if item.get("useAprDay"):
        parts.append(f"사용승인: {item['useAprDay']}")
    if item.get("bldNm"):
        parts.append(f"건물명: {item['bldNm']}")

    content = " | ".join(parts)
    url = f"https://www.eais.go.kr/build/min_search.do?ref={mgm_pk}"

    return Article(
        id=_make_id(mgm_pk),
        source="eais.go.kr",
        url=url,
        title=title,
        content=content,
        published_at=_parse_date_yyyymmdd(arch_day),
    )


def _is_new_or_extension(arch_gb: str) -> bool:
    """archGbCdNm 가 신축/증축 인지 — 그 외 (대수선/용도변경/이전/개축/재축) 는 컷."""
    s = (arch_gb or "").strip()
    if not s:
        # 누락은 통과 (보수적 — 영업 기회 놓치지 않음)
        return True
    return any(k in s for k in VALID_ARCH_GB_KEYWORDS)


def filter_and_convert(
    items: list[dict],
    location_label: str,
    threshold_str: str,
    threshold_man: int,
    filter_purpose: bool,
    filter_cost: bool,
    dong_categories: list[str] | None = None,
) -> tuple[list[Article], int]:
    """items → Articles. (articles, n_industrial_seen) 반환.

    필터 체인:
      1) 용도 (공장/창고/연구소/...) — filter_purpose
      2) 인허가일 ≥ threshold_str
      3) archGbCdNm = 신축/증축 (대수선·용도변경·이전 컷)
      4) 연면적 outlier (>100만㎡) 컷
      5) 추정공사비 ≥ threshold_man — filter_cost
      6) (platPlc + bldNm) 중복 제거 — 같은 사업장의 변경이력 중 최신만 keep

    dong_categories: 이 동이 속한 산업 클러스터 (반도체_디스플레이/이차전지/...).
                     단가 추정 시 1차 신호로 사용.
    n_industrial_seen: 산업시설 후보가 몇 건 있었는지 (date 무관, dedup 전).
                      → 캐시 인덱스의 has_industrial 판정에 사용.
    """
    # (article, item, archPmsDay) — dedup 후보
    candidates: list[tuple[Article, dict, str]] = []
    industrial_seen = 0

    for it in items:
        purpose = it.get("mainPurpsCdNm", "") or ""
        is_industrial = _is_target_purpose(purpose)
        if is_industrial:
            industrial_seen += 1

        # 1) 용도 필터
        if filter_purpose and not is_industrial:
            continue
        # 2) 인허가일 필터 — 날짜 미상/손상은 컷.
        #    윈도우 판정 불가한 건(인허가일 빈값)을 통과시키면 어느 윈도우에서나
        #    똑같이 뜨고 매주 반복 노출됨 → '진짜 최근 N일' 보장을 위해 컷.
        #    ('50140514' 같은 손상 날짜도 _sane_archpms_day 가 None 반환 → 컷)
        d = _sane_archpms_day(it.get("archPmsDay", "") or "")
        if d is None:
            continue
        if d < threshold_str:
            continue
        # 3) 신축/증축 필터
        if not _is_new_or_extension(it.get("archGbCdNm", "") or ""):
            continue
        # 4) 연면적 outlier 컷 (100만㎡ 초과는 단위 오류·산단 합산)
        try:
            area_v = float(str(it.get("totArea") or "0").replace(",", ""))
        except ValueError:
            area_v = 0.0
        if area_v > MAX_AREA_M2:
            logger.debug("연면적 outlier 컷: %.0f㎡ @ %s", area_v, it.get("platPlc", ""))
            continue
        # 5) 추정공사비
        cost_man, category, _ = estimate_cost_man(
            purpose,
            it.get("totArea"),
            it.get("bldNm", "") or "",
            it.get("platPlc", "") or "",
            dong_categories=dong_categories,
        )
        if filter_cost and not passes_threshold(cost_man, threshold_man):
            continue
        # 상한 컷 — 2조+ 는 부지/단지 합산 아티팩트 (거제 ~99만㎡ 등). 영업 타겟(450억~2조) 밖.
        if filter_cost and cost_man >= MAX_COST_MAN:
            continue
        art = _to_article(it, location_label, cost_man, category)
        if art is None:
            continue
        candidates.append((art, it, d))

    # 6) (platPlc + bldNm) dedup — 같은 사업장의 변경이력은 archPmsDay 가장 최신만.
    #    bldNm 빈 경우 mgmPmsrgstPk 까지 키에 포함 (개별 인허가 보존).
    best_by_key: dict[str, tuple[Article, dict, str]] = {}
    for art, it, d in candidates:
        plat = (it.get("platPlc") or "").strip()
        bld = (it.get("bldNm") or "").strip()
        if plat and bld:
            key = f"{plat}||{bld}"
        else:
            # 식별 불가 → 개별 인허가 보존
            key = f"_uniq_{it.get('mgmPmsrgstPk', art.id)}"
        prev = best_by_key.get(key)
        if prev is None or d > prev[2]:
            best_by_key[key] = (art, it, d)

    out = [v[0] for v in best_by_key.values()]
    return out, industrial_seen


# ─────────────────────────────────────────────────────────
# 5. 메인 collect — 캐시 우선, 부족분만 API
# ─────────────────────────────────────────────────────────


def collect(
    days: int,
    verify_ssl: bool,
    *,
    quota: int = DEFAULT_QUOTA,
    threshold_man: int = DEFAULT_THRESHOLD_MAN,
    filter_purpose: bool = True,
    filter_cost: bool = True,
    full_sweep: bool = False,
    only_sigungu: str | None = None,
    only_bjdong: str | None = None,
    refresh_active_days: int = DEFAULT_REFRESH_ACTIVE_DAYS,
    refresh_empty_days: int = DEFAULT_REFRESH_EMPTY_DAYS,
    use_industrial: bool = True,
) -> list[Article]:
    """타겟 전국 동/읍/면 신규 인허가 수집.

    1) 캐시에 있는 동: 캐시 데이터로 필터링 (API 호출 0)
    2) 캐시 없거나 stale 한 동: API 호출 후 캐시 갱신
    3) quota 소진 시 (1) 결과만 사용, (2) 는 다음 run 에 넘김
    """
    load_dotenv()
    api_key = os.environ.get("EAIS_API_KEY", "").strip()
    if not api_key:
        logger.error("EAIS_API_KEY 가 .env 에 설정되지 않았습니다 — docs/API_SETUP.md §3")
        return []

    legal_dongs = load_legal_dongs(use_industrial=use_industrial)
    if not legal_dongs:
        return []

    # 디버그 모드: 특정 동 1개만
    if only_sigungu and only_bjdong:
        legal_dongs = [d for d in legal_dongs
                       if d["sigungu_cd"] == only_sigungu and d["bjdong_cd"] == only_bjdong]
        if not legal_dongs:
            # CSV 에 없는 동도 디버그용으로 허용
            legal_dongs = [{
                "sigungu_cd": only_sigungu, "bjdong_cd": only_bjdong,
                "sido": "", "sigungu": "", "dong": "",
                "full_nm": f"{only_sigungu}-{only_bjdong} [디버그]",
            }]
        full_sweep = True  # 디버그는 quota 무시

    index = load_index()
    now = datetime.now(KST)
    threshold_dt = now - timedelta(days=days)
    threshold_str = threshold_dt.strftime("%Y%m%d")

    logger.info("EAIS 수집: days=%d (>=%s), threshold=%.0f억, quota=%d, full_sweep=%s, 전국 동=%d",
                days, threshold_str, threshold_man / 10000, quota, full_sweep, len(legal_dongs))

    # 1) 쿼리 대상 결정
    if full_sweep:
        to_query = legal_dongs
    else:
        to_query = pick_dongs_to_query(legal_dongs, index, quota, now,
                                       refresh_active_days, refresh_empty_days)
    to_query_keys = {f"{d['sigungu_cd']}-{d['bjdong_cd']}" for d in to_query}

    # 2) API 조회 + 캐시 갱신
    articles: list[Article] = []
    seen_ids: set[str] = set()
    api_calls_used = 0
    api_failed = 0
    dongs_queried = 0
    dongs_with_results = 0

    for dong in to_query:
        if not full_sweep and api_calls_used >= quota:
            logger.info("Quota %d 도달 — API 조회 중단 (캐시 데이터만 활용)", quota)
            break
        sigungu_cd, bjdong_cd = dong["sigungu_cd"], dong["bjdong_cd"]
        label = dong.get("full_nm") or f"{sigungu_cd}-{bjdong_cd}"

        items, total, calls, err = fetch_dong_full(api_key, sigungu_cd, bjdong_cd, verify_ssl)
        api_calls_used += calls
        dongs_queried += 1
        if err:
            api_failed += 1
            logger.warning("  ✗ %s (%s-%s): %s", label, sigungu_cd, bjdong_cd, err)
            continue

        # 필터링 (동의 산업 클러스터 카테고리 → 단가 추정에 사용)
        dong_cats = [c for c in (dong.get("categories", "") or "").split("|") if c]
        loc_arts, industrial_seen = filter_and_convert(
            items, label, threshold_str, threshold_man, filter_purpose, filter_cost,
            dong_categories=dong_cats or None,
        )
        kept = 0
        for a in loc_arts:
            if a.id in seen_ids:
                continue
            seen_ids.add(a.id)
            articles.append(a)
            kept += 1
        if kept > 0:
            dongs_with_results += 1

        # 캐시 갱신 (전체 items 보관)
        save_cache(sigungu_cd, bjdong_cd, {
            "sigungu_cd": sigungu_cd, "bjdong_cd": bjdong_cd,
            "full_nm": label,
            "fetched_at": now.isoformat(),
            "total_count": total,
            "page_count": calls,
            "items": items,
        })
        index[f"{sigungu_cd}-{bjdong_cd}"] = {
            "fetched_at": now.isoformat(),
            "total_count": total,
            "has_industrial": industrial_seen > 0,
            "industrial_seen": industrial_seen,
        }

        if industrial_seen > 0 or kept > 0:
            logger.info("  ✓ %s (%s-%s): total=%d, 산업후보=%d, 통과=%d, calls=%d",
                        label, sigungu_cd, bjdong_cd, total, industrial_seen, kept, calls)

    # 3) 캐시만 있고 이번 run 에 안 건드린 동에서도 필터링 적용 (날짜 컷)
    cache_used = 0
    cache_kept = 0
    for dong in legal_dongs:
        key = f"{dong['sigungu_cd']}-{dong['bjdong_cd']}"
        if key in to_query_keys:
            continue  # 위에서 처리됨
        cache = load_cache(dong["sigungu_cd"], dong["bjdong_cd"])
        if cache is None:
            continue
        cache_used += 1
        items = cache.get("items", [])
        label = cache.get("full_nm") or key
        dong_cats = [c for c in (dong.get("categories", "") or "").split("|") if c]
        loc_arts, _ = filter_and_convert(
            items, label, threshold_str, threshold_man, filter_purpose, filter_cost,
            dong_categories=dong_cats or None,
        )
        for a in loc_arts:
            if a.id in seen_ids:
                continue
            seen_ids.add(a.id)
            articles.append(a)
            cache_kept += 1

    save_index(index)

    logger.info("─" * 60)
    logger.info("EAIS 완료: API %d회 사용 (%d 동 조회, 실패 %d, 결과있음 %d)",
                api_calls_used, dongs_queried, api_failed, dongs_with_results)
    logger.info("           캐시 활용 %d 동 → 추가 %d건", cache_used, cache_kept)
    logger.info("           최종 Article: %d건 (450억+/30일+/용도필터 통과)", len(articles))
    return articles


# ─────────────────────────────────────────────────────────
# 6. CLI
# ─────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="건축HUB 인허가 수집기 (전국 + 450억 필터)")
    parser.add_argument("--days", type=int, default=7,
                        help="최근 N일 인허가만 (기본 7 — 주 1회 보고 사이클)")
    parser.add_argument("--output", help="출력 JSONL 경로 (생략 시 data/raw/eais_{date}.jsonl)")
    parser.add_argument("--insecure", action="store_true", help="SSL 검증 비활성화")
    parser.add_argument("--quota", type=int, default=DEFAULT_QUOTA,
                        help=f"이번 run 의 최대 API 호출 수 (기본 {DEFAULT_QUOTA})")
    parser.add_argument("--full-sweep", action="store_true",
                        help="quota 무시하고 후보 동 전부 조회 (주의: 1,000회/일 한도)")
    parser.add_argument("--threshold-eok", type=float, default=450,
                        help="추정공사비 임계값 (억) (기본 450)")
    parser.add_argument("--refresh-active-days", type=int, default=DEFAULT_REFRESH_ACTIVE_DAYS,
                        help=f"활성 동 재조회 주기 (기본 {DEFAULT_REFRESH_ACTIVE_DAYS}일)")
    parser.add_argument("--refresh-empty-days", type=int, default=DEFAULT_REFRESH_EMPTY_DAYS,
                        help=f"빈 동 재조회 주기 (기본 {DEFAULT_REFRESH_EMPTY_DAYS}일)")
    parser.add_argument("--sigungu", help="특정 시군구코드만 (디버그용, --bjdong 필요)")
    parser.add_argument("--bjdong", help="특정 법정동코드만 (--sigungu 와 함께)")
    parser.add_argument("--no-purpose-filter", action="store_true",
                        help="용도 필터 비활성화 (디버그용)")
    parser.add_argument("--no-cost-filter", action="store_true",
                        help="추정공사비 필터 비활성화 (450억 미만도 포함)")
    parser.add_argument("--all-dongs", action="store_true",
                        help="전국 5,961개 동 모두 (기본: 산업 후보 ~1,200동만)")
    args = parser.parse_args()

    if args.insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    threshold_man = int(args.threshold_eok * 10000)

    articles = collect(
        days=args.days,
        verify_ssl=not args.insecure,
        quota=args.quota,
        threshold_man=threshold_man,
        filter_purpose=not args.no_purpose_filter,
        filter_cost=not args.no_cost_filter,
        full_sweep=args.full_sweep,
        only_sigungu=args.sigungu,
        only_bjdong=args.bjdong,
        refresh_active_days=args.refresh_active_days,
        refresh_empty_days=args.refresh_empty_days,
        use_industrial=not args.all_dongs,
    )

    if args.output:
        out = Path(args.output)
    else:
        today = datetime.now(KST).strftime("%Y-%m-%d")
        out = Path(f"data/raw/eais_{today}.jsonl")

    written = write_jsonl(out, articles)
    logger.info("─" * 60)
    logger.info("Stage 0 (EAIS) 완료: %d건 → %s", written, out)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
